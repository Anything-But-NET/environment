import os, json, time, uuid
from pathlib import Path
import pytest
from kafka import KafkaProducer
from kafka.errors import KafkaError
import psycopg2
from psycopg2.errors import UndefinedTable

KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'localhost:9092')
KAFKA_TOPIC = os.getenv('KAFKA_TOPIC', 'rag.documents.ingestion.v1')
DB_HOST, DB_PORT, DB_NAME = os.getenv('DB_HOST', 'localhost'), os.getenv('DB_PORT', '5432'), os.getenv('DB_NAME', 'rag_db')
DB_USER, DB_PASS = os.getenv('DB_USER', 'postgres'), os.getenv('DB_PASS', 'postgres')
FIXTURES_DIR = Path(__file__).parent / "fixtures"

@pytest.fixture(scope="session")
def kafka_producer():
    try:
        producer = KafkaProducer(
            bootstrap_servers=[KAFKA_BROKER],
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            acks='all',
            retries=1
        )
        yield producer
        producer.close()
    except KafkaError:
        pytest.skip("Skipped E2E tests: Kafka not available")

@pytest.fixture(scope="session")
def db_connection():
    try:
        conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
        conn.autocommit = True
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM document_metadata LIMIT 1")
        yield conn
        conn.close()
    except psycopg2.OperationalError:
        pytest.skip("Skipped E2E tests: PostgreSQL not available on CI-server")
    except UndefinedTable:
        pytest.skip("Skipped E2E tests: Table document_metadata not available")

def load_fixture(filename: str, doc_id: str = None) -> dict:
    with open(FIXTURES_DIR / filename, 'r', encoding='utf-8') as f: data = json.load(f)
    if doc_id:
        data["document_id"] = doc_id
        data["url"] = f"https://company.sharepoint.com/docs/{doc_id}"
    return data

def send(producer, event, key):
    producer.send(KAFKA_TOPIC, key=key.encode('utf-8'), value=event).get(timeout=10)

def fetch_document(conn, doc_id):
    with conn.cursor() as cur:
        cur.execute("SELECT document_id, title, allowed_groups FROM document_metadata WHERE document_id = %s", (doc_id,))
        return cur.fetchone()

def wait_for_document_in_db(conn, doc_id: str, timeout: int = 15):
    start = time.time()
    while time.time() - start < timeout:
        row = fetch_document(conn, doc_id)
        if row: return row
        time.sleep(0.5)
    return None

def barrier(producer, conn, key, timeout=30):
    probe_id = f"probe-{uuid.uuid4()}"
    send(producer, load_fixture("valid.json", probe_id), key)
    assert wait_for_document_in_db(conn, probe_id, timeout) is not None, "Pipeline stopped"

def assert_metadata(row, expected):
    assert row is not None, "Document is not found in DB"
    assert row[1] == expected["title"], f"Expected title '{expected['title']}', received '{row[1]}'"
    assert set(row[2] or []) == set(expected["allowed_groups"]), "ACL doesn't match"

def test_checker_fails_on_wrong_title():
    expected = load_fixture("valid.expected.json")
    with pytest.raises(AssertionError, match="Expected title"):
        assert_metadata(("dummy-id", "WRONG TITLE", expected["allowed_groups"]), expected)

@pytest.mark.e2e
def test_valid_document_ingestion(kafka_producer, db_connection):
    barrier(kafka_producer, db_connection, "warmup-key")
    doc_id = f"valid-{uuid.uuid4()}"
    send(kafka_producer, load_fixture("valid.json", doc_id), doc_id)
    assert_metadata(wait_for_document_in_db(db_connection, doc_id), load_fixture("valid.expected.json"))

@pytest.mark.e2e
def test_duplicate_event_is_idempotent(kafka_producer, db_connection):
    doc_id = f"dup-{uuid.uuid4()}"
    event = load_fixture("valid.json", doc_id)
    send(kafka_producer, event, doc_id)
    send(kafka_producer, event, doc_id)
    barrier(kafka_producer, db_connection, doc_id)
    with db_connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM document_metadata WHERE document_id = %s", (doc_id,))
        assert cur.fetchone()[0] == 1

@pytest.mark.e2e
def test_update_overwrites_metadata(kafka_producer, db_connection):
    doc_id = f"upd-{uuid.uuid4()}"
    event = load_fixture("valid.json", doc_id)
    send(kafka_producer, event, doc_id)
    barrier(kafka_producer, db_connection, doc_id)

    updated_event = load_fixture("update.json", doc_id)
    send(kafka_producer, updated_event, doc_id)
    barrier(kafka_producer, db_connection, doc_id)

    assert count_documents(db_connection, doc_id) == 1
    row = fetch_document(db_connection, doc_id)
    assert row[1] == updated_event["title"]
    assert set(row[2] or []) == set(updated_event["acl"]["allowed_groups"])

@pytest.mark.e2e
@pytest.mark.parametrize("fixture_file", ["empty_content.json", "malformed_no_acl.json"])
def test_bad_event_does_not_block_pipeline(kafka_producer, db_connection, fixture_file):
    doc_id = f"bad-{uuid.uuid4()}"
    send(kafka_producer, load_fixture(fixture_file, doc_id), doc_id)
    barrier(kafka_producer, db_connection, doc_id)