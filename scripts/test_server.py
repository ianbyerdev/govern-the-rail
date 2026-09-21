import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import uvicorn
from saac.api import create_app

def main():
    folder = Path(tempfile.mkdtemp(prefix="saac-browser-"))
    app = create_app(folder)
    # The test runner alone reads this ephemeral local credential; it is not an API.
    (Path(__file__).resolve().parent.parent / "frontend/.test-token").write_text(app.state.operator_token)
    uvicorn.run(app, host="127.0.0.1", port=8011)


if __name__ == '__main__':
    # The C8 fixture uses multiprocessing spawn. Importing the parent launcher
    # in a child must not start another web server or replace its credential.
    main()
