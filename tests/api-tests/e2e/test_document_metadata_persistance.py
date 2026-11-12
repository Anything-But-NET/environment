import pytest
# TODO: Заміни 'your_backend_module' на реальний шлях до моделей у вашому проєкті
from your_backend_module import DocumentRepository, ProcessingStatus, DocumentMetadata

def test_new_document_persists_all_metadata(db_session):
    """
    AC 1: A new document persists its stable source ID, title, source link, 
    and processing status.
    """
    repo = DocumentRepository(db_session)
    
    repo.upsert_document(
        source_id="doc-001",
        title="Річний звіт 2023",
        source_link="https://docs.local/report23.pdf",
        status=ProcessingStatus.SUCCESS
    )
    
    doc = repo.get_by_source_id("doc-001")
    
    assert doc is not None
    assert doc.title == "Річний звіт 2023"
    assert doc.source_link == "https://docs.local/report23.pdf"
    assert doc.status == ProcessingStatus.SUCCESS
    
    # Verification: Документ успішно створено з усіма полями

def test_reingesting_updates_existing_record_without_duplicate(db_session):
    """
    AC 2: Re-ingesting the same source ID updates the existing record 
    without creating a duplicate.
    """
    repo = DocumentRepository(db_session)
    
    repo.upsert_document("doc-002", "Стара назва", "http://link", ProcessingStatus.PENDING)
    repo.upsert_document("doc-002", "Нова назва", "http://link", ProcessingStatus.SUCCESS)
    
    all_docs_with_id = db_session.query(DocumentMetadata).filter_by(source_id="doc-002").all()
    
    assert len(all_docs_with_id) == 1
    assert all_docs_with_id[0].title == "Нова назва"
    assert all_docs_with_id[0].status == ProcessingStatus.SUCCESS
    
    # Verification: У базі залишився лише 1 запис з оновленими даними

def test_status_persistence_and_querying(db_session):
    """
    AC 3: Success and failure statuses are persisted and can be queried.
    """
    repo = DocumentRepository(db_session)
    
    repo.upsert_document("doc-10", "Title 10", "link10", ProcessingStatus.SUCCESS)
    repo.upsert_document("doc-11", "Title 11", "link11", ProcessingStatus.FAILURE)
    repo.upsert_document("doc-12", "Title 12", "link12", ProcessingStatus.SUCCESS)
    
    successful_docs = repo.get_by_status(ProcessingStatus.SUCCESS)
    failed_docs = repo.get_by_status(ProcessingStatus.FAILURE)
    
    assert len(successful_docs) == 2
    assert len(failed_docs) == 1
    assert failed_docs[0].source_id == "doc-11"
    
    # Verification: Запити по статусах повертають правильну кількість документів
