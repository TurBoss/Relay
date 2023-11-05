Relay
=====

Simple _irc_ to _Matrix_ bridge


How-to
======

Just copy `config_sample.yal` to `config.yaml` and modify it


Setting up the python venv


```python
python3 -m venv venv
source venv/bin/activate
pip install matrix-nio
pip install git+https://github.com/TotallyNotRobots/async-irc.git

python relay.py config.yaml
```

