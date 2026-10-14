import subprocess
import sys

patterns = [
    r'from fastapi',
    r'import fastapi',
    r'FastAPI\(',
    r'ReactDOM',
    r'createRoot\(',
    r'from [\'"]react',
    r'react-dom',
    r'sqlalchemy',
    r'from alembic\b',
    r'import alembic\b',
    r':8787'
]

print("=== AUDITING ACTIVE APPLICATION SOURCE FOR LEGACY TRACES ===")
found_any = False
for pat in patterns:
    # Exclude docs, tests, backups, markdown files, and git history/lockfiles
    cmd = ['git', 'grep', '-n', '-i', '-E', pat, '--', ':!docs', ':!tests', ':!backups', ':!*.md', ':!package-lock.json', ':!*.json']
    res = subprocess.run(cmd, capture_output=True, text=True)
    out = res.stdout.strip()
    if out:
        # Filter out comments if any or check lines
        lines = [line for line in out.splitlines() if not line.startswith('scripts/audit_legacy.py')]
        if lines:
            print(f"\n[FOUND MATCHES FOR '{pat}']:")
            for l in lines:
                print("  " + l)
            found_any = True

if not found_any:
    print("\nSUCCESS: ZERO legacy patterns found in active application source!")
else:
    print("\nWARNING: Legacy patterns found above!")
