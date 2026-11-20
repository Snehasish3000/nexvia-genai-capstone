import pytest

from app.generation import ABSTENTION


@pytest.mark.asyncio
async def test_answer_has_trusted_source_citation(runtime):
    _, _, service, _ = runtime
    result = await service.answer(
        "What is the maximum manager purchase approval?", "finance-user", "finance", "hybrid"
    )
    assert not result.abstained
    assert "[Source: finance_policy.docx" in result.answer
    assert result.citations
    assert result.citations[0].source == "finance_policy.docx"


@pytest.mark.asyncio
async def test_cache_is_scoped_by_role(runtime):
    _, _, service, _ = runtime
    finance_result = await service.answer("What is the maximum manager purchase approval?", "f1", "finance", "hybrid")
    engineering_result = await service.answer("What is the maximum manager purchase approval?", "e1", "engineering", "hybrid")
    assert not finance_result.abstained
    assert engineering_result.abstained
    assert not engineering_result.cache_hit


@pytest.mark.asyncio
async def test_no_evidence_causes_deterministic_abstention(runtime):
    store, _, service, _ = runtime
    store._rows.clear()
    result = await service.answer("What is the approved employee bonus percentage?", "guest", "guest", "hybrid")
    assert result.abstained
    assert result.answer == ABSTENTION
    assert result.citations == []
