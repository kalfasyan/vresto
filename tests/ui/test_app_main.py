"""Tests for the web app entry point."""

from unittest.mock import MagicMock, patch

from vresto.ui import app as app_module


def test_main_prewarms_scene_index_tile_pool_and_s3():
    hooks = []
    with (
        patch.object(app_module.app, "on_startup", side_effect=lambda fn: hooks.append(fn) or fn),
        patch.object(app_module.ui, "run"),
        patch.object(app_module, "register_tile_proxy"),
        patch.object(app_module, "is_public_mode", return_value=False),
    ):
        app_module.main()

    assert [h.__name__ for h in hooks] == ["_prewarm"]
    thread = MagicMock()
    with patch.object(app_module.threading, "Thread", return_value=thread) as make_thread:
        hooks[0]()
    names = [call.kwargs["name"] for call in make_thread.call_args_list]
    assert names == ["scene-index-prewarm", "tile-pool-prewarm", "sentinel-s3-prewarm"]
    assert thread.start.call_count == 3
