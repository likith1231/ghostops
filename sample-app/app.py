from flask import Flask, jsonify

app = Flask(__name__)
leak_store = []

@app.route("/health")
def health():
    return jsonify(status="ok", leaked_objects=len(leak_store))

@app.route("/leak")
def leak():
    return jsonify(status="leaked", total_mb=len(leak_store) * 10)

@app.route("/")
def index():
    return jsonify(service="ghostops-sample-app", version="1.0")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
