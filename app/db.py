import logging

import psycopg2
from psycopg2.extras import execute_batch

from app.config import Settings

logger = logging.getLogger("RUSADA.db")


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._conn = None

    @property
    def conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                host=self.settings.db_host,
                database=self.settings.db_name,
                user=self.settings.db_user,
                password=self.settings.db_password,
                sslmode=self.settings.db_sslmode,
            )
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def init_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS rusada;")
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS rusada.questions (
                    id SERIAL PRIMARY KEY,
                    text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    correct_answer_ids INTEGER[]
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS rusada.answers (
                    id SERIAL PRIMARY KEY,
                    question_id INTEGER REFERENCES rusada.questions(id) ON DELETE CASCADE,
                    text TEXT NOT NULL
                );
                """
            )
        self.conn.commit()
        logger.info("Database schema ready")

    def find_answer_indices(self, question_text: str, page_answers: list[str]) -> list[int]:
        try:
            row = None
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT correct_answer_ids FROM rusada.questions WHERE text = %s",
                    (question_text,),
                )
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        "SELECT correct_answer_ids FROM rusada.questions "
                        "WHERE similarity(text, %s) > 0.85 "
                        "ORDER BY similarity(text, %s) DESC LIMIT 1",
                        (question_text, question_text),
                    )
                    row = cur.fetchone()

            if row and row[0]:
                with self.conn.cursor() as cur:
                    cur.execute("SELECT text FROM rusada.answers WHERE id = ANY(%s)", (row[0],))
                    db_answers = [r[0].strip().lower() for r in cur.fetchall()]
                return [
                    i
                    for i, page_answer in enumerate(page_answers)
                    if any(da in page_answer.strip().lower() for da in db_answers)
                ]
        except Exception as exc:
            logger.error("Answer lookup failed: %s", exc)
        return []

    def save_question(self, question_text: str, answers: list[str]) -> int | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM rusada.questions WHERE text = %s", (question_text,))
            candidates = [r[0] for r in cur.fetchall()]

            for qid in candidates:
                cur.execute("SELECT text FROM rusada.answers WHERE question_id = %s", (qid,))
                existing = {r[0] for r in cur.fetchall()}
                if existing == set(answers):
                    return None

            cur.execute(
                "INSERT INTO rusada.questions (text) VALUES (%s) RETURNING id",
                (question_text,),
            )
            new_id = cur.fetchone()[0]
            execute_batch(
                cur,
                "INSERT INTO rusada.answers (question_id, text) VALUES (%s, %s)",
                [(new_id, a) for a in answers],
            )
        self.conn.commit()
        logger.info("Saved question %s", new_id)
        return new_id

    def get_unmarked_question(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id, text FROM rusada.questions "
                "WHERE correct_answer_ids IS NULL OR correct_answer_ids = '{}' "
                "ORDER BY id LIMIT 1"
            )
            return cur.fetchone()

    def get_answers(self, question_id: int) -> list[tuple[int, str]]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id, text FROM rusada.answers WHERE question_id = %s ORDER BY id",
                (question_id,),
            )
            return cur.fetchall()

    def mark_question(self, question_id: int, correct_ids: list[int]) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE rusada.questions SET correct_answer_ids = %s WHERE id = %s",
                (correct_ids, question_id),
            )
        self.conn.commit()
        logger.info("Marked question %s: %s", question_id, correct_ids)

    def stats(self) -> tuple[int, int]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM rusada.questions "
                "WHERE correct_answer_ids IS NOT NULL AND correct_answer_ids != '{}'"
            )
            marked = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM rusada.questions "
                "WHERE correct_answer_ids IS NULL OR correct_answer_ids = '{}'"
            )
            unmarked = cur.fetchone()[0]
        return marked, unmarked
