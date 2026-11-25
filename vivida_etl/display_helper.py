"""
vivida_etl/display_helper.py
Cross-platform virtual display helper for Selenium/browser scrapers.
Avoids Linux-only Xvfb failures ([WinError 2]) on Windows while preserving
headless Xvfb support on Linux when no DISPLAY is already active.
"""

from contextlib import contextmanager
import logging
import os
import sys
from typing import Iterator, Optional, Tuple

logger = logging.getLogger("vivida_etl.display")

try:
    from pyvirtualdisplay import Display as _XvfbDisplay
except ImportError:  # pragma: no cover - optional on non-Linux hosts
    _XvfbDisplay = None


@contextmanager
def virtual_display(
    size: Tuple[int, int] = (1920, 1080),
    visible: int = 0,
    backend: str = "xvfb",
) -> Iterator[Optional[object]]:
    """
    Context manager that starts a virtual display only when needed on Linux.

    - Windows / macOS: yields None (Chrome/WebDriver runs directly).
    - Linux with DISPLAY already set (e.g., xvfb-run in CI): yields None.
    - Linux without DISPLAY: starts pyvirtualdisplay.Display and stops it on exit.
    """
    should_start_xvfb = (
        sys.platform.startswith("linux")
        and not os.environ.get("DISPLAY")
        and _XvfbDisplay is not None
    )

    display = None
    if should_start_xvfb:
        try:
            display = _XvfbDisplay(visible=visible, size=size, backend=backend)
            display.start()
        except Exception as exc:
            logger.warning(f"Failed to start virtual display ({backend}): {exc}")
            display = None

    try:
        yield display
    finally:
        if display is not None:
            try:
                display.stop()
            except Exception as exc:
                logger.debug(f"Error stopping virtual display: {exc}")