@echo off
bump-my-version bump patch
python -c "import re; v=re.search(r'(?m)^version = \"([\d.]+)\"', open('cifflow_core/Cargo.toml').read()).group(1); t=open('Cargo.lock').read(); t=re.sub(r'(name = \"cifflow_core\"\nversion = \")[\d.]+\"', r'\g<1>'+v+'\"', t); open('Cargo.lock','w',newline='\n').write(t)"
git add Cargo.lock
git commit --amend --no-edit
git push && git push --tags
