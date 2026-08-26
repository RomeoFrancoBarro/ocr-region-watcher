import unittest
from unittest.mock import call, patch

from ocr_region_watcher import inject


class SendRestoresMousePositionTests(unittest.TestCase):
    """inject.send() clicks (x, y) to give the target focus, but the click
    itself is a side effect -- it should never leave the OS-wide mouse
    cursor sitting wherever the target happened to be. Reported directly:
    after Send, the mouse stayed at the last click position instead of
    going back to wherever it started.
    """

    @patch("ocr_region_watcher.inject.pyautogui")
    @patch("ocr_region_watcher.inject.pyperclip")
    def test_click_and_paste_restores_original_mouse_position(self, pyperclip, pyautogui):
        pyautogui.position.return_value = (11, 22)

        inject.send(100, 200, "hello", click=True, paste=True)

        # Clicked the target, pasted, then moved back to where the mouse
        # started -- in that order.
        pyautogui.click.assert_called_once_with(100, 200)
        pyperclip.copy.assert_called_once_with("hello")
        pyautogui.hotkey.assert_called_once_with("ctrl", "v")
        pyautogui.moveTo.assert_called_once_with(11, 22)

        expected_order = [call.position(), call.click(100, 200), call.hotkey("ctrl", "v"), call.moveTo(11, 22)]
        self.assertEqual(pyautogui.mock_calls, expected_order)

    @patch("ocr_region_watcher.inject.pyautogui")
    @patch("ocr_region_watcher.inject.pyperclip")
    def test_click_only_still_restores_position(self, pyperclip, pyautogui):
        pyautogui.position.return_value = (5, 5)

        inject.send(100, 200, click=True, paste=False)

        pyautogui.moveTo.assert_called_once_with(5, 5)

    @patch("ocr_region_watcher.inject.pyautogui")
    @patch("ocr_region_watcher.inject.pyperclip")
    def test_paste_only_never_moves_mouse(self, pyperclip, pyautogui):
        # click=False means this function never moved the mouse in the
        # first place, so there's nothing to restore.
        inject.send(100, 200, "hello", click=False, paste=True)

        pyautogui.moveTo.assert_not_called()
        pyautogui.position.assert_not_called()

    @patch("ocr_region_watcher.inject.pyautogui")
    @patch("ocr_region_watcher.inject.pyperclip")
    def test_restores_position_even_if_paste_raises(self, pyperclip, pyautogui):
        pyautogui.position.return_value = (7, 9)
        pyperclip.copy.side_effect = RuntimeError("clipboard unavailable")

        with self.assertRaises(RuntimeError):
            inject.send(100, 200, "hello", click=True, paste=True)

        pyautogui.moveTo.assert_called_once_with(7, 9)


if __name__ == "__main__":
    unittest.main()
