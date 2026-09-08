# Everything CI runs, runnable in one command before you push.
.PHONY: check test test-python test-js manifest validate audit clean

check: test manifest validate
	@echo "all checks passed"

test: test-python test-js

# No PATH: the helper shells out to op, wl-copy and wtype, and a test that
# quietly takes a different branch when those are missing is a test that only
# passes on the author's machine. CI has neither.
test-python:
	@env -u PATH $$(command -v python3) -m unittest discover -s tests -q

test-js:
	@node --test "tests/js/*.test.mjs"

manifest:
	@python3 tools/check_manifest.py

# The shell's own loader checks, so a manifest the running Omarchy would
# reject cannot be committed.
validate:
	@omarchy plugin validate . && echo "omarchy validate: ok"

# Renders every UI state offscreen and screenshots it. Needs a running
# Hyprland session; see tools/audit/README.md.
audit:
	@tools/audit/shoot-all.sh

clean:
	@find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
