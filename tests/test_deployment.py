"""Deployment guards run without a database or external provider calls (unittest)."""
import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deployment_host', ROOT / 'scripts/deployment_host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class DeploymentGuards(unittest.TestCase):
    def config(self):
        return {'name': 'fixture', 'services': {
            'api': {'environment': {'DJANGO_SECRET_KEY': 's' * 50, 'STUDIO_PASSWORD': 'p' * 20,
                                     'STUDIO_DOMAIN': 'studio.example.com', 'REEL_DATA_DIR': '/app/data'},
                    'ports': [{'published': '8002', 'host_ip': '127.0.0.1'}]},
            'postgres': {'environment': {'POSTGRES_PASSWORD': 'x' * 32, 'POSTGRES_DB': 'studio', 'POSTGRES_USER': 'postgres'}}}}

    def test_edunexa_port_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'EduNexa'):
            host.validate(self.config())

    def test_secret_strength(self):
        config = self.config()
        config['services']['api']['environment']['DJANGO_SECRET_KEY'] = 'weak'
        with self.assertRaisesRegex(ValueError, 'DJANGO_SECRET_KEY'):
            host.validate(config)

    def test_missing_password_rejected(self):
        config = self.config()
        config['services']['api']['environment'].pop('STUDIO_PASSWORD')
        with self.assertRaisesRegex(ValueError, 'STUDIO_PASSWORD'):
            host.validate(config)

    def test_insufficient_disk_rejected(self):
        from types import SimpleNamespace
        config = self.config()
        config['services']['api'].pop('ports')
        with patch.object(host.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ValueError, 'Insufficient free disk'):
                host.validate(config)

    def test_occupied_port_rejected(self):
        import socket
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            config = self.config()
            config['services']['api']['ports'][0]['published'] = str(listener.getsockname()[1])
            with self.assertRaisesRegex(ValueError, 'occupied'):
                host.validate(config)

    def test_database_url_delimiters_rejected_without_echo(self):
        config = self.config()
        config['services']['postgres']['environment']['POSTGRES_PASSWORD'] = 'sensitive:password@123'
        with self.assertRaisesRegex(ValueError, 'URL-safe') as error:
            host.validate(config)
        self.assertNotIn('sensitive', str(error.exception))

    def test_foreign_project_is_rejected(self):
        config = self.config()
        config['services']['api']['container_name'] = 'studio-api'
        with patch.object(host, 'inspect', return_value={'Config': {'Labels': {'com.docker.compose.project': 'other'}}}):
            with self.assertRaisesRegex(ValueError, 'another Compose project'):
                host.validate(config)

    def test_volume_replacement_is_rejected(self):
        config = self.config()
        config['services']['api'].pop('ports')
        pg = config['services']['postgres']
        pg['container_name'] = 'studio-postgres'
        pg['volumes'] = [{'source': 'postgres_data', 'target': '/var/lib/postgresql', 'type': 'volume'}]
        config['volumes'] = {'postgres_data': {'name': 'new_empty_volume'}}
        actual = {'Config': {'Labels': {'com.docker.compose.project': 'fixture', 'com.docker.compose.service': 'postgres'}},
                  'Mounts': [{'Destination': '/var/lib/postgresql', 'Name': 'original_production_data'}]}
        with patch.object(host, 'inspect', return_value=actual):
            with self.assertRaisesRegex(ValueError, 'persistent volume mismatch'):
                host.validate(config)

    def test_retention_preserves_unrelated_and_partial_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('studio-20260101T000000-1.dump', 'studio-20260102T000000-1.dump',
                         'unrelated.dump', 'studio-partial.dump.partial', 'studio-not-ours.dump'):
                (root / name).write_text('fixture')
            host.retention(folder, 1)
            self.assertTrue((root / 'unrelated.dump').exists())
            self.assertTrue((root / 'studio-partial.dump.partial').exists())
            self.assertTrue((root / 'studio-not-ours.dump').exists())
            self.assertEqual(len(list(root.glob('studio-2026*.dump'))), 1)
            with self.assertRaises(ValueError):
                host.retention(folder, 0)


@unittest.skipUnless(os.name == 'posix', 'Bash/flock workflow tests run on Linux')
class ShellWorkflowGuards(unittest.TestCase):
    """Real Bash control flow with fake Docker/Git; no production side effects."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'scripts', self.root / 'scripts')
        (self.root / '.env').write_text('fixture-only')
        (self.root / '.deployment').mkdir()
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        docker = bin_dir / 'docker'
        docker.write_text('''#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$TRACE"
if [[ "$1" == inspect && "$*" == *com.docker.compose.project* ]]; then exit 1; fi
if [[ "$*" == *io.video-studio.deployment-contract* ]]; then printf '%s' "${TEST_CONTRACT-1}"; exit 0; fi
if [[ "$*" == *'ps --status running --services'* ]]; then printf 'api\\nworker_render\\n'; exit 0; fi
if [[ "$*" == *'SELECT count(*)'* ]]; then echo "${TEST_JOBS:-0}"; exit 0; fi
if [[ "$*" == *'migrate --check'* && "${TEST_SCHEMA_FAIL:-0}" == 1 ]]; then exit 1; fi
if [[ "$*" == *pg_dump* ]]; then printf fixture; fi
exit 0
''')
        git = bin_dir / 'git'
        git.write_text('''#!/usr/bin/env bash
if [[ "$*" == 'status --porcelain' ]]; then printf '%s' "${TEST_DIRTY:-}"; fi
''')
        for executable in (docker, git):
            executable.chmod(0o755)
        self.env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                        TRACE=str(self.root / 'trace'))

    def shell(self, command, **env):
        return subprocess.run(['bash', '-c', 'source scripts/deployment-common.sh; ' + command],
                              cwd=self.root, env=dict(self.env, **env), capture_output=True, text=True)

    def trace(self):
        return (self.root / 'trace').read_text()

    def test_active_jobs_abort_before_stopping_api(self):
        result = self.shell('quiesce', TEST_JOBS='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('stop api', self.trace())

    def test_idle_update_stops_admission_before_workers(self):
        result = self.shell('quiesce')
        self.assertEqual(result.returncode, 0, result.stderr)
        trace = self.trace()
        self.assertLess(trace.index('stop api'), trace.index('stop dispatcher'))
        self.assertEqual(trace.count('SELECT count(*)'), 2)

    def test_dirty_git_aborts(self):
        result = self.shell('clean_git', TEST_DIRTY=' M tracked-file')
        self.assertNotEqual(result.returncode, 0)

    def test_failed_schema_check_does_not_restart_old_application(self):
        import hashlib
        (self.root / '.deployment/previous-revision').write_text('a' * 64)
        (self.root / '.deployment/previous-schema').write_text(hashlib.sha256(b'').hexdigest())
        result = subprocess.run(['bash', 'scripts/rollback.sh'], cwd=self.root,
                                env=dict(self.env, TEST_SCHEMA_FAIL='1'), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('migrate --check', self.trace())
        self.assertNotIn('up -d', self.trace())

    def test_backup_failure_resumes_only_previously_running_services(self):
        result = self.shell('quiesce; false')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('start api worker_render', self.trace())
        self.assertNotIn('start api worker_network', self.trace())

    def test_schema_boundary_refuses_rollback_before_stopping_services(self):
        (self.root / '.deployment/previous-revision').write_text('a' * 64)
        (self.root / '.deployment/previous-schema').write_text('different')
        result = subprocess.run(['bash', 'scripts/rollback.sh'], cwd=self.root,
                                env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('stop api', self.trace())

    def test_legacy_image_refuses_rollback_before_stopping_services(self):
        (self.root / '.deployment/previous-revision').write_text('a' * 64)
        result = subprocess.run(['bash', 'scripts/rollback.sh'], cwd=self.root,
                                env=dict(self.env, TEST_CONTRACT=''), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('predates the supported deployment contract', result.stderr)
        self.assertNotIn('stop api', self.trace())
        self.assertNotIn('pg_dump', self.trace())

    def test_active_rollback_image_survives_maintenance_invocations(self):
        release = 'rollback-' + 'a' * 64
        (self.root / '.deployment/active-release').write_text(release)
        result = self.shell('printf "%s" "$STUDIO_RELEASE"')
        self.assertEqual(result.stdout, release)

    def test_restore_refuses_production_target(self):
        result = subprocess.run(['bash', 'scripts/restore.sh', '.env', '--into-new-database', 'studio'],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('createdb', self.trace())

    def test_concurrent_operation_refused(self):
        import fcntl
        with (self.root / '.deployment/operation.lock').open('w') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.shell('lock')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Another deployment', result.stderr)


if __name__ == '__main__':
    unittest.main()
