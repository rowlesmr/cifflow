@echo off
set PYTHONUTF8=1
bump-my-version bump patch
python -c "import re; v=re.search(r'(?m)^version = \"([\d.]+)\"', open('cifflow_core/Cargo.toml').read()).group(1); t=open('Cargo.lock').read(); t=re.sub(r'(name = \"cifflow_core\"\nversion = \")[\d.]+\"', r'\g<1>'+v+'\"', t); open('Cargo.lock','w',newline='\n').write(t)"
git add Cargo.lock
git commit --amend --no-edit
for /f "delims=" %%v in ('python -c "import re; print(re.search(r\"(?m)^version = \\\"([\\d.]+)\\\"\", open('pyproject.toml').read()).group(1))"') do (
    git checkout -b release/v%%v
    git push origin release/v%%v
    gh pr create --title "Release v%%v" --base main --head release/v%%v --body "Version bump to v%%v [skip ci]"
)
pause