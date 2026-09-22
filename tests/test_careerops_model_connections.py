"""Connection controls/receipts tested with mocks: no remote inference or auth changes."""
import json
from pathlib import Path
import subprocess

import pytest
from careerops import model_connections as connections


def test_environment_preserves_login_home_and_drops_billing_and_injected_config(monkeypatch):
    monkeypatch.setenv('HOME', '/native/home')
    monkeypatch.setenv('CODEX_HOME', '/native/codex')
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_MODEL', 'NODE_OPTIONS', 'BASH_ENV'):
        monkeypatch.setenv(key, 'must-not-inherit')
    env = connections._environment()
    assert env['HOME'] == '/native/home'
    assert env['CODEX_HOME'] == '/native/codex'
    assert not any(value == 'must-not-inherit' for value in env.values())


def test_codex_catalog_disables_native_tools_before_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(connections.shutil, 'which', lambda name: '/mock/' + name)
    native = {'slug': 'gpt-6-astra', 'shell_type': 'unified_exec', 'apply_patch_tool_type': 'freeform',
              'experimental_supported_tools': ['clock'], 'tool_mode': 'code_mode_only'}
    monkeypatch.setattr(connections, '_probe', lambda args: (0, json.dumps({'models': [native]})))
    argv = connections._command({'provider': 'codex_cli', 'model': 'gpt-6-astra', 'effort': 'high'}, tmp_path, {})
    catalog = json.loads((tmp_path / 'model-catalog.json').read_text())['models'][0]
    assert catalog['slug'] == 'gpt-6-astra'
    assert catalog['shell_type'] == 'disabled'
    assert catalog['apply_patch_tool_type'] is None
    assert catalog['node_repl_disabled'] is True
    assert catalog['tool_mode'] == 'direct'
    assert catalog['multi_agent_version'] == 'disabled'
    assert catalog['experimental_supported_tools'] == []
    assert '--ignore-user-config' in argv and '--ignore-rules' in argv and '--ephemeral' in argv
    assert argv[argv.index('--sandbox') + 1] == 'read-only'
    assert 'shell_tool' in argv and 'hooks' in argv and 'plugins' in argv
    overrides = dict(arg.split('=', 1) for i, arg in enumerate(argv) if i and argv[i - 1] == '-c')
    # These are independent capability gates: feature flags alone do not disable
    # model-catalog agents, orchestrator skill readers or request_user_input.
    for key in ('agents.enabled', 'tools.experimental_request_user_input.enabled',
                'tools.update_plan.enabled', 'orchestrator.skills.enabled',
                'orchestrator.mcp.enabled', 'skills.include_instructions'):
        assert overrides[key] == 'false'
    assert 'tools.view_image' not in overrides
    assert 'view_image' in argv
    assert not any('forced_login_method' in arg for arg in argv)
    assert not any('dangerously' in arg for arg in argv)


def test_claude_disables_tools_hooks_mcp_without_disabling_oauth(tmp_path, monkeypatch):
    monkeypatch.setattr(connections.shutil, 'which', lambda name: '/mock/' + name)
    argv = connections._command({'provider': 'claude_cli', 'model': 'claude-opus-5', 'effort': 'high'}, tmp_path, {})
    assert '--safe-mode' in argv and '--bare' not in argv
    assert argv[argv.index('--tools') + 1] == ''
    assert '--strict-mcp-config' in argv
    assert json.loads(argv[argv.index('--mcp-config') + 1]) == {'mcpServers': {}}
    settings = json.loads(argv[argv.index('--settings') + 1])
    assert settings['disableAllHooks'] is True
    assert settings['availableModels'] == ['claude-opus-5']
    assert settings['fallbackModel'] == []
    assert settings['switchModelsOnFlag'] is False
    assert 'modelFallbacks' not in settings
    assert '--fallback-model' not in argv
    assert '--no-session-persistence' in argv


def test_unknown_native_model_fails_before_start(tmp_path, monkeypatch):
    monkeypatch.setattr(connections.shutil, 'which', lambda name: '/mock/' + name)
    monkeypatch.setattr(connections, '_probe', lambda args: (0, '{"models":[]}'))
    with pytest.raises(connections.ConnectionError, match='tool-free catalog'):
        connections._command({'provider': 'codex_cli', 'model': 'unknown', 'effort': 'high'}, tmp_path, {})


@pytest.fixture
def fake_process(monkeypatch):
    result = {'is_error': False, 'modelUsage': {'claude-opus-5': {'inputTokens': 10}},
              'structured_output': {'summary': 'Mock response'}, 'usage': {'input_tokens': 10}}
    monkeypatch.setattr(connections, 'detected', lambda **kwargs: {'claude_cli': {'ready': True, 'cli_version': 'mock', 'auth_method': 'claude.ai'}})
    monkeypatch.setattr(connections, '_command', lambda *args: ['mock-cli'])
    class Process:
        pid = 0
        returncode = 0
        def __init__(self, argv, **kwargs): self.output = kwargs['stdout']
        def wait(self, timeout=None): self.output.write(json.dumps(result)); self.output.flush(); return 0
        def poll(self): return 0
    monkeypatch.setattr(connections.subprocess, 'Popen', Process)
    return result


def test_actual_model_receipt_and_usage_from_provider(fake_process):
    result = connections.execute({'provider': 'claude_cli', 'model': 'claude-opus-5', 'effort': 'high'}, {'synthetic': True}, {}, 'blue', timeout_seconds=30)
    assert result['actual_model'] == 'claude-opus-5'
    assert result['model_identity_source'] == 'provider_model_usage'
    assert result['tool_calls'] == 0
    assert result['usage']['tokens']['input_tokens'] == 10


def test_silent_model_substitution_is_never_success(fake_process):
    fake_process['modelUsage'] = {'claude-other': {}}
    with pytest.raises(connections.ConnectionError) as error:
        connections.execute({'provider': 'claude_cli', 'model': 'claude-opus-5', 'effort': 'high'}, {}, {}, 'blue', timeout_seconds=30)
    assert error.value.uncertain


def test_failed_reviewer_is_never_success(fake_process):
    fake_process['is_error'] = True
    with pytest.raises(connections.ConnectionError):
        connections.execute({'provider': 'claude_cli', 'model': 'claude-opus-5', 'effort': 'high'}, {}, {}, 'blue', timeout_seconds=30)


def test_settings_reject_billing_routes_and_raise_concurrency():
    base = {**{role: {'provider': 'codex_cli', 'model': 'gpt-6-astra', 'effort': 'high'} for role in connections.ROLES}, 'timeout_seconds': 180}
    with pytest.raises(ValueError, match='one CV run'):
        connections.validate(dict(base, max_concurrent_runs=2))
    base['blue'] = {'provider': 'anthropic_api', 'model': 'claude-opus-5'}
    with pytest.raises(ValueError, match='existing Codex or Claude'):
        connections.validate(base)


def _synthetic_python_adapter(monkeypatch, body):
    """A local Python child, never a model CLI, OAuth flow or network request."""
    import sys
    monkeypatch.setattr(connections, 'detected', lambda **kwargs: {'claude_cli': {
        'ready': True, 'cli_version': 'synthetic-python-child', 'auth_method': 'mocked'}})
    monkeypatch.setattr(connections, '_command', lambda *args: [sys.executable, '-c', body])
    return {'provider': 'claude_cli', 'model': 'claude-opus-5', 'effort': 'high'}


def test_slow_child_receives_prompt_larger_than_pipe_buffer_and_eof(monkeypatch):
    # Child deliberately waits longer than the parent's 200 ms polling period.
    # The former communicate(prompt)/communicate(None) implementation stalled
    # because the remaining pipe input and EOF were never delivered.
    connection = _synthetic_python_adapter(monkeypatch, '''
import sys, time, json
time.sleep(.45)
packet = json.load(sys.stdin)
print(json.dumps({'modelUsage': {'claude-opus-5': {}},
 'structured_output': {'received_characters': len(packet['data']['synthetic_text'])},
 'is_error': False, 'usage': {}}))
''')
    size = 220_000
    response = connections.execute(connection, {'synthetic_text': 'x' * size}, {}, 'blue', timeout_seconds=30)
    assert response['response'] == {'received_characters': size}
    assert response['elapsed_seconds'] < 5


def test_local_child_cancellation_remains_bounded(monkeypatch):
    import threading
    connection = _synthetic_python_adapter(monkeypatch, 'import sys,time; sys.stdin.read(); time.sleep(30)')
    cancelled = threading.Event()
    timer = threading.Timer(.1, cancelled.set)
    timer.start()
    try:
        with pytest.raises(connections.ConnectionError) as error:
            connections.execute(connection, {'synthetic_text': 'x' * 100_000}, {}, 'blue', cancel_event=cancelled, timeout_seconds=30)
        assert error.value.cancelled and error.value.uncertain
    finally:
        timer.cancel()


def test_local_child_timeout_is_enforced(monkeypatch):
    connection = _synthetic_python_adapter(monkeypatch, 'import sys,time; sys.stdin.read(); time.sleep(30)')
    ticks = iter([0, 31, 32, 33])
    monkeypatch.setattr(connections.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(connections.ConnectionError, match='timed out') as error:
        connections.execute(connection, {'synthetic_text': 'x' * 100_000}, {}, 'blue', timeout_seconds=30)
    assert error.value.uncertain


def test_completed_child_cannot_bypass_output_limit(monkeypatch):
    connection = _synthetic_python_adapter(monkeypatch, 'import sys; sys.stdin.read(); sys.stdout.write("x" * 4000001)')
    with pytest.raises(connections.ConnectionError, match='response limit') as error:
        connections.execute(connection, {}, {}, 'blue', timeout_seconds=30)
    assert error.value.uncertain
