"""Execute the distributed beginner notebook and verify its saved outputs."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path


def test_quickstart_notebook_runs_without_checkout_relative_inputs(
    tmp_path: Path, monkeypatch,
) -> None:
    notebook_path = (
        Path(__file__).resolve().parents[1]
        / "examples" / "notebooks" / "quickstart.ipynb"
    )
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    monkeypatch.chdir(tmp_path)
    namespace = {"__name__": "__main__"}
    execution_count = 0
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        execution_count += 1
        assert cell["execution_count"] == execution_count
        output = io.StringIO()
        with redirect_stdout(output):
            exec(
                compile("".join(cell["source"]), f"notebook-cell-{execution_count}", "exec"),
                namespace,
            )
        saved = cell["outputs"]
        assert all(
            item["output_type"] == "stream" and item["name"] == "stdout"
            for item in saved
        )
        assert output.getvalue() == "".join(
            "".join(item["text"]) for item in saved
        )
    assert execution_count > 0
    assert not list(tmp_path.iterdir()), "The notebook must leave no working files"
