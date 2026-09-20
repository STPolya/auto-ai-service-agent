"""Real Russian FTS on a disposable local cluster, never a configured database.

Skipped when PostgreSQL server binaries are absent. No DATABASE_URL or .env is read.
The cluster listens only on loopback and is stopped/deleted after this test class.
"""

from pathlib import Path
import shutil
import socket
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from app.database.models import KnowledgeChunk
from app.rag.ingestion import ingest_knowledge
from app.rag.retriever import retrieve_context


class PostgreSQLRetrievalTests(unittest.TestCase):
    def test_support_handoff_race_and_partial_unique_index(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from sqlalchemy.exc import IntegrityError
        from app.database.models import User, Conversation, Message, SupportRequest
        from app.services.support_request_service import create_support_request
        from app.services.support_status import IN_PROGRESS, RESOLVED

        for model in (User, Conversation, Message, SupportRequest):
            model.__table__.create(self.engine, checkfirst=True)
        with self.factory.begin() as session:
            user = User(telegram_id=12345)
            session.add(user)
            session.flush()
            conversation = Conversation(user_id=user.id)
            session.add(conversation)
            session.flush()
            user_id, conversation_id = user.id, conversation.id
        barrier = Barrier(2)
        def summarize(history):
            barrier.wait(timeout=10)
            # Both initial reads are closed, and neither writer can begin yet.
            self.assertEqual(self.engine.pool.checkedout(), 0)
            barrier.wait(timeout=10)
            return "Причина обращения не уточнена."
        with patch("app.services.support_request_service.get_session_factory", return_value=self.factory), \
             patch("app.services.support_request_service.summarize_handoff", side_effect=summarize):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(create_support_request, 12345, conversation_id) for _ in range(2)]
                results = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(result.created for result in results), [False, True])
        self.assertEqual(results[0].request.id, results[1].request.id)
        with self.assertRaises(IntegrityError):
            with self.factory.begin() as session:
                session.add(SupportRequest(user_id=user_id, conversation_id=conversation_id, status=IN_PROGRESS))
        with self.factory.begin() as session:
            session.get(SupportRequest, results[0].request.id).status = RESOLVED
        with self.factory.begin() as session:
            session.add(SupportRequest(user_id=user_id, conversation_id=conversation_id, status=IN_PROGRESS))
        with self.factory() as session:
            self.assertEqual(len(list(session.scalars(select(SupportRequest)))), 2)

    @classmethod
    def setUpClass(cls):
        executable = shutil.which("initdb")
        if not executable:
            candidates = sorted(Path("C:/Program Files/PostgreSQL").glob("*/bin/initdb.exe"))
            executable = str(candidates[-1]) if candidates else None
        if not executable:
            raise unittest.SkipTest("Local PostgreSQL binaries unavailable; SQL compilation tests still run.")
        binary = Path(executable).parent
        suffix = ".exe" if Path(executable).suffix == ".exe" else ""
        cls.temporary = TemporaryDirectory(prefix="autocare-rag-test-")
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        data = directory / "data"
        # Do not pipe output: Windows server children can retain pipe handles
        # after pg_ctl exits, causing subprocess.communicate() to wait forever.
        options = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "timeout": 30}
        if suffix:
            options["creationflags"] = subprocess.CREATE_NO_WINDOW

        def run(arguments):
            result = subprocess.run([str(arg) for arg in arguments], **options)
            if result.returncode:
                raise RuntimeError("Isolated PostgreSQL test cluster command failed.")

        run([executable, "-D", data, "-A", "trust", "-U", "rag_test", "--encoding=UTF8", "--locale=C"])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        pg_ctl = binary / ("pg_ctl" + suffix)
        def stop_cluster():
            if (data / "postmaster.pid").exists():
                run([pg_ctl, "-D", data, "-m", "immediate", "-w", "stop"])
        cls.addClassCleanup(stop_cluster)
        run([pg_ctl, "-D", data, "-l", directory / "server.log", "-o",
             f"-h 127.0.0.1 -p {port} -F", "-w", "start"])
        cls.engine = create_engine(URL.create("postgresql+psycopg", username="rag_test", host="127.0.0.1",
                                             port=port, database="postgres"), echo=False, hide_parameters=True)
        cls.addClassCleanup(cls.engine.dispose)
        # Create only test schema from metadata; never invoke Alembic upgrade.
        KnowledgeChunk.__table__.create(cls.engine)
        cls.factory = sessionmaker(bind=cls.engine, expire_on_commit=False)

    def setUp(self):
        with self.engine.begin() as connection:
            connection.execute(KnowledgeChunk.__table__.delete())
        for target in ("app.rag.retriever.get_session_factory", "app.rag.ingestion.get_session_factory"):
            patcher = patch(target, return_value=self.factory)
            patcher.start()
            self.addCleanup(patcher.stop)

    def add(self, source, content, active=True):
        with self.factory.begin() as session:
            session.add(KnowledgeChunk(source=source, title="Справка", content=content,
                                       category="test", is_active=active))

    def test_russian_natural_language_retrieves_brakes_with_stemming(self):
        ingest_knowledge()
        results = retrieve_context("При торможении вибрация руля — что проверить?!")
        self.assertTrue(results)
        self.assertEqual(results[0].source, "brakes.md")
        self.assertLessEqual(len(results), 4)

    def test_inactive_excluded_limit_enforced_ties_ordered_by_id(self):
        self.add("inactive.md", "торможение торможение", False)
        for index in range(7):
            self.add(f"active{index}.md", "торможение")
        self.assertEqual([item.source for item in retrieve_context("торможении", limit=100)],
                         [f"active{index}.md" for index in range(4)])
        self.assertEqual(len(retrieve_context("торможении", limit=2)), 2)

    def test_rank_precedes_id_and_queries_are_safe(self):
        self.add("lower.md", "торможение")
        self.add("higher.md", "торможение вибрация")
        self.assertEqual(retrieve_context("торможение вибрация")[0].source, "higher.md")
        self.assertEqual(retrieve_context("несуществующееслово"), [])
        self.assertEqual(retrieve_context("и в на"), [])
        retrieve_context("'; DROP TABLE knowledge_chunks; --")
        with self.factory() as session:
            self.assertEqual(len(list(session.scalars(select(KnowledgeChunk)))), 2)

    def test_manual_ingestion_is_idempotent_on_postgresql(self):
        count = ingest_knowledge()
        with self.factory() as session:
            ids = list(session.scalars(select(KnowledgeChunk.id).order_by(KnowledgeChunk.id)))
        self.assertEqual(ingest_knowledge(), count)
        with self.factory() as session:
            self.assertEqual(list(session.scalars(select(KnowledgeChunk.id).order_by(KnowledgeChunk.id))), ids)
