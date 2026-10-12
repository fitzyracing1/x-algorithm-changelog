# Getting started

```bash
git clone https://github.com/fitzyracing1/x-algorithm-changelog && cd x-algorithm-changelog
python -m xchangelog                 # clones upstream into .cache/x-algorithm, writes ./site
python scripts/validate.py site      # Atom + HTML + internal-link checks
python -m http.server -d site 8000
python -m unittest discover -s tests # bot tests
```
