"""Recover completed AI drafts into blank projects. Stop the studio before running."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


def recover(database):
    database = Path(database)
    if not database.is_file():
        raise RuntimeError('Database not found. Copy this tool into your Studio folder beside run.py.')
    backup = database.with_name('studio-before-recovery-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
    c = sqlite3.connect(database, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        with sqlite3.connect(backup) as destination:
            c.backup(destination)
        c.execute('BEGIN IMMEDIATE')
        restored = []
        for row in c.execute('SELECT * FROM projects').fetchall():
            project = json.loads(row['body'])
            scenes = project.get('scenes', [])
            if any(s.get('narration', '').strip() or s.get('roman', '').strip() or s.get('video_asset') or s.get('audio_asset') for s in scenes):
                continue  # Never overwrite edited or media-assigned scenes.
            candidates = c.execute("SELECT * FROM jobs WHERE project_id=? AND kind='draft' AND state='succeeded' ORDER BY created DESC", (row['id'],)).fetchall()
            for job in candidates:
                result = json.loads(job['result'])
                recovered = result.get('scenes', [])
                if not isinstance(recovered, list) or not recovered:
                    continue
                if not all(isinstance(s, dict) and isinstance(s.get('narration'), str) and s['narration'].strip() and isinstance(s.get('roman'), str) and s['roman'].strip() for s in recovered):
                    continue
                project['scenes'] = recovered
                revision = row['revision'] + 1
                body = json.dumps(project, ensure_ascii=False)
                when = datetime.now(timezone.utc).isoformat()
                c.execute('UPDATE projects SET body=?,revision=?,approved=NULL,updated=? WHERE id=?', (body, revision, when, row['id']))
                c.execute('INSERT INTO revisions(project_id,revision,body,created) VALUES(?,?,?,?)', (row['id'], revision, body, when))
                restored.append((len(recovered), revision))
                break
        c.commit()
        return restored, backup
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


if __name__ == '__main__':
    try:
        changed, backup = recover(Path(__file__).resolve().parent / 'data' / 'studio.sqlite3')
        print('Backup created:', backup.name)
        if changed:
            for count, revision in changed:
                print(f'Recovered {count} scenes into project revision {revision}.')
            print('Start the studio, refresh the browser, and open Script editor.')
            print('The recovered draft still needs your factual review and approval.')
        else:
            print('No blank project with a valid completed draft was found. Existing work was preserved.')
    except Exception as exc:
        print('Recovery stopped:', str(exc))
        raise SystemExit(1)
