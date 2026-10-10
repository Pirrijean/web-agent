import asyncio
from types import SimpleNamespace

from web_agent.core.runner import AgentRunner
from web_agent.core.schemas import (
    ActionType,
    FinalState,
    ProposedAction,
    TaskProfile,
)


class FakePage:
    def __init__(self):
        self.url = "https://example.com"
        self.navigations = 0
        self.title_reads = 0

    def is_closed(self):
        return False

    async def title(self):
        self.title_reads += 1
        return "Página"

    async def goto(self, url, **kwargs):
        self.navigations += 1
        self.url = url
        return SimpleNamespace(status=200)


class FakeBrowser:
    def __init__(self, fail_start=False):
        self.page = FakePage()
        self.fail_start = fail_start
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True
        if self.fail_start:
            raise RuntimeError("Fallo parcial")

    async def stop(self):
        self.stopped = True


class FakeAudit:
    def __init__(self, fail=False):
        self.fail = fail
        self.events = []

    def log_event(self, event):
        if self.fail:
            raise OSError("No disponible")
        self.events.append(event)


class OneActionModel:
    def __init__(self, action):
        self.action = action
        self.calls = 0
        self.page_state = None

    async def propose_action(self, objective, page_state):
        self.calls += 1
        self.page_state = page_state
        return self.action


def make_profile(**overrides):
    data = {
        "id": "test",
        "security": {"allowed_origins": ["https://example.com"]},
    }
    data.update(overrides)
    return TaskProfile.model_validate(data)


def test_invalid_model_type_is_rejected_without_navigation():
    browser = FakeBrowser()
    runner = AgentRunner(
        make_profile(),
        OneActionModel(object()),
        browser,
        FakeAudit(),
    )

    result = asyncio.run(runner.run("Comprobar estado"))

    assert result.final_state == FinalState.FAILED
    assert browser.page.navigations == 0
    assert browser.stopped


def test_constructed_done_with_forbidden_fields_is_rejected():
    forged = ProposedAction.model_construct(
        action_type=ActionType.DONE,
        target_element_id="e1",
        value=None,
        url=None,
        reasoning="Finalizado",
    )
    browser = FakeBrowser()

    result = asyncio.run(
        AgentRunner(
            make_profile(),
            OneActionModel(forged),
            browser,
            FakeAudit(),
        ).run("Comprobar estado")
    )

    assert result.final_state == FinalState.FAILED
    assert browser.page.navigations == 0


def test_missing_audit_blocks_browser_start():
    browser = FakeBrowser()
    model = OneActionModel({
        "action_type": "navigate",
        "url": "https://example.com",
        "reasoning": "Consultar",
    })

    result = asyncio.run(
        AgentRunner(
            make_profile(), model, browser, FakeAudit(fail=True)
        ).run("Consultar página")
    )

    assert result.final_state == FinalState.BLOCKED
    assert not browser.started
    assert model.calls == 0


def test_partial_start_failure_still_closes_resources():
    browser = FakeBrowser(fail_start=True)

    asyncio.run(
        AgentRunner(
            make_profile(),
            OneActionModel(None),
            browser,
            FakeAudit(),
        ).run("Consultar página")
    )

    assert browser.started
    assert browser.stopped


def test_read_permission_prevents_title_read():
    browser = FakeBrowser()
    model = OneActionModel({
        "action_type": "ask_human",
        "reasoning": "Se necesita intervención",
    })

    result = asyncio.run(
        AgentRunner(
            make_profile(permissions={"read_visible_data": False}),
            model,
            browser,
            FakeAudit(),
        ).run("Consultar página")
    )

    assert result.final_state == FinalState.BLOCKED
    assert browser.page.title_reads == 0
