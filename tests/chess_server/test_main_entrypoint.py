"""The `python -m chess_server` entrypoint (role spec §8.6)."""
import chess_server.__main__ as entry


def test_build_app_exposes_the_api_and_dashboard(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "arena.db"))
    monkeypatch.setenv("JOIN_CODE", "join")
    monkeypatch.setenv("ADMIN_TOKEN", "admin-token")

    app = entry.build_app()
    try:
        paths = set(app.openapi()["paths"])
        assert "/health" in paths
        assert "/bots" in paths
        assert "/dashboard" in {getattr(r, "path", None) for r in app.routes}
    finally:
        app.state.arena.store.close()

    assert "admin-token" not in capsys.readouterr().out


def test_missing_join_code_is_actionable_not_a_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "arena.db"))
    monkeypatch.delenv("JOIN_CODE", raising=False)
    monkeypatch.setenv("ADMIN_TOKEN", "admin-token")

    assert entry.main(["--port", "8123"]) == 2

    err = capsys.readouterr().err
    assert "JOIN_CODE is empty" in err
    assert "Traceback" not in err


def test_run_prints_both_urls_and_no_admin_token(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "arena.db"))
    monkeypatch.setenv("JOIN_CODE", "join")
    monkeypatch.setenv("ADMIN_TOKEN", "admin-token")

    served = {}

    def _fake_run(app, **kwargs):
        served.update(kwargs)
        app.state.arena.store.close()

    monkeypatch.setattr(entry.uvicorn, "run", _fake_run)

    assert entry.main(["--host", "0.0.0.0", "--port", "8123"]) == 0
    assert (served["host"], served["port"]) == ("0.0.0.0", 8123)

    out = capsys.readouterr().out
    assert "http://0.0.0.0:8123" in out
    assert "http://0.0.0.0:8123/dashboard/" in out
    assert "admin-token" not in out


def test_defaults_are_localhost_8000():
    args = entry.parse_args([])
    assert (args.host, args.port) == ("127.0.0.1", 8000)
