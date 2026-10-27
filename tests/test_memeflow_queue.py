"""Exercise the desktop batch controller against real video exports."""
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from services.local_clips import run_media
import threading
import tools.memeflow as studio


class QueueTests(unittest.TestCase):
    def test_batch_continues_after_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source, background = directory / "meme.mp4", directory / "background.mp4"
            for file, color in ((source, "red"), (background, "blue")):
                run_media(["-y", "-f", "lavfi", "-i", f"color=c={color}:size=160x100:rate=30",
                           "-t", "0.6", "-c:v", "libx264", str(file)], threading.Event())
            second = directory / "second.mp4"
            import shutil
            shutil.copyfile(source, second)
            root = tk.Tk()
            root.withdraw()
            try:
                with patch.object(studio, "STATE", directory / "state"):
                    app = studio.MemeFlow(root)
                    app.vars["background"].set(str(background))
                    app.vars["output"].set(str(directory / "outputs"))
                    app.banner_enabled.set(False)
                    app.add_paths([source, directory / "missing.mp4", second])
                    app.start()
                    deadline = time.monotonic()+30
                    while app.busy and time.monotonic() < deadline:
                        root.update()
                        time.sleep(.03)
                    self.assertFalse(app.busy)
                    self.assertEqual(len(app.outputs), 2)
                    self.assertEqual(len(app.errors), 1)
                    self.assertIn("Готово", app.tree.set("0", "status"))
                    self.assertIn("Ошибка", app.tree.set("1", "status"))
                    self.assertIn("Готово", app.tree.set("2", "status"))
                    self.assertEqual(len(list((directory / "outputs").glob("*.mp4"))), 2)
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
