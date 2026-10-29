"""真实 Git 维护不得脱离调用方；子进程探针主动回收失败场景的后台进程。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = textwrap.dedent(
    """
    import ctypes
    import json
    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    from fde_asset.platform.repo.gitea_git import GiteaGitRepo
    from fde_asset.platform.repo.local_git import LocalGitRepo

    if ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) != 0:
        raise RuntimeError('cannot enable child subreaper')
    def timeout(_signal, _frame):
        raise TimeoutError('Git probe timed out')
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(8)
    root = Path(sys.argv[1])
    mode = sys.argv[2]
    for name in ('remote.git', 'cache.git'):
        subprocess.run(
            ['git', 'init', '--bare', '-q', '--initial-branch', 'main', str(root / name)],
            check=True, capture_output=True, timeout=5,
        )
    cache = root / 'cache.git'
    # 仓库自行启用后台维护时，服务仍必须以命令级配置收束其生命周期。
    for key in ('maintenance.auto', 'maintenance.autoDetach', 'gc.autoDetach'):
        subprocess.run(
            ['git', 'config', key, 'true'],
            cwd=cache, check=True, capture_output=True, timeout=5,
        )
    args = ['fetch', '--quiet', '--prune', '--no-tags', str(root / 'remote.git'),
            '+refs/heads/*:refs/heads/*']
    detached = []
    try:
        if mode == 'local':
            LocalGitRepo(root)._git(args, cache)
        else:
            GiteaGitRepo(root, base_url='http://unused.invalid', owner='probe',
                         token='synthetic-test-token')._remote_git(args, cache)
    finally:
        signal.alarm(0)
        deadline = time.monotonic() + 3
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid:
                detached.append({'pid': pid, 'status': status})
            elif time.monotonic() >= deadline:
                children = Path('/proc/self/task') / str(os.getpid()) / 'children'
                for child in children.read_text().split():
                    try:
                        os.kill(int(child), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                while True:
                    try:
                        os.waitpid(-1, 0)
                    except ChildProcessError:
                        break
                raise RuntimeError('background maintenance did not terminate')
            else:
                time.sleep(0.01)
    print(json.dumps({'detached': detached}))
    """
)


@unittest.skipUnless(sys.platform == "linux", "requires Linux child subreaper")
class GitMaintenanceTests(unittest.TestCase):
    def check_fetch(self, mode: str) -> None:
        with tempfile.TemporaryDirectory(prefix="fde-git-maintenance-test-") as tmp:
            result = subprocess.run(
                [sys.executable, "-c", PROBE, tmp, mode],
                env={
                    "PATH": os.defpath + ":/usr/local/bin",
                    "HOME": tmp,
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "PYTHONPATH": str(ROOT / "src"),
                },
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["detached"], [], result.stdout)

    def test_local_fetch_keeps_maintenance_in_foreground(self) -> None:
        self.check_fetch("local")

    def test_remote_fetch_keeps_maintenance_in_foreground(self) -> None:
        self.check_fetch("remote")


if __name__ == "__main__":
    unittest.main()
