@echo off
set PYTHONUTF8=1
bump-my-version bump patch
python -c "import re; v=re.search(r'(?m)^version = \"([\d.]+)\"', open('cifflow_core/Cargo.toml').read()).group(1); t=open('Cargo.lock').read(); t=re.sub(r'(name = \"cifflow_core\"\nversion = \")[\d.]+\"', r'\g<1>'+v+'\"', t); open('Cargo.lock','w',newline='\n').write(t)"
git add Cargo.lock
git commit --amend --no-edit
python -c "import re; open('.ver.tmp','w').write(re.search(r'(?m)^version = \"([\d.]+)\"', open('pyproject.toml').read()).group(1))"
set /p RELEASE_VER= < .ver.tmp
del .ver.tmp
git checkout -b release/v%RELEASE_VER%
git push origin release/v%RELEASE_VER%
gh pr create --title "Release v%RELEASE_VER%" --base main --head release/v%RELEASE_VER% --body "Version bump to v%RELEASE_VER% [skip ci]"
pause