from flask import Flask, jsonify
from collections import deque

app = Flask(__name__)

# In-memory cache bounded to 1000 most-recent entries
MAX_CACHE_SIZE = 1000
request_cache = deque(maxlen=MAX_CACHE_SIZE)


@app.route('/api/data')
def get_data():
    result = expensive_computation()
    request_cache.append(result)
    return jsonify(result)


def expensive_computation():
    return {'value': list(range(10000))}


if __name__ == '__main__':
    app.run(debug=True)
