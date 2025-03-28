import json
import boto3
import os
import logging
from flask import Flask, Response, request
from flask_sse import sse
from werkzeug.serving import run_simple
import sqlparse

# Set up logging
logging.basicConfig(
    filename='mcp_audit.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.register_blueprint(sse, url_prefix="/events")

# AWS client for Redshift
redshift_client = boto3.client('redshift-data', region_name='us-east-1')

# Redshift configuration
CLUSTER_ID = os.getenv("REDSHIFT_CLUSTER_ID", "<your-redshift-cluster-id>")
DATABASE = os.getenv("REDSHIFT_DATABASE", "<your-database-name>")
DB_USER = os.getenv("REDSHIFT_DB_USER", "<your-db-user>")

# API key for authentication
VALID_API_KEY = os.getenv("MCP_API_KEY", "your-secure-api-key-here")

def is_select_only(query):
    parsed = sqlparse.parse(query)
    for statement in parsed:
        if statement.get_type().upper() != "SELECT":
            return False
    return True

def run_redshift_query(query):
    logger.info(f"Executing query: {query}")
    if not is_select_only(query):
        logger.warning(f"Rejected non-SELECT query: {query}")
        return {"error": "Only SELECT statements are allowed"}
    
    try:
        response = redshift_client.execute_statement(
            ClusterIdentifier=CLUSTER_ID,
            Database=DATABASE,
            DbUser=DB_USER,
            Sql=query
        )
        statement_id = response['Id']

        while True:
            status = redshift_client.describe_statement(Id=statement_id)['Status']
            if status in ['FINISHED', 'FAILED', 'ABORTED']:
                break

        if status == 'FINISHED':
            result = redshift_client.get_statement_result(Id=statement_id)
            rows = [dict(zip([col['name'] for col in result['ColumnMetadata']], row)) 
                    for row in result['Records']]
            logger.info(f"Query succeeded: {query} - Returned {len(rows)} rows")
            return {"result": rows}
        else:
            logger.error(f"Query failed with status: {status} - Query: {query}")
            return {"error": f"Query failed with status: {status}"}
    except Exception as e:
        logger.error(f"Query execution error: {str(e)} - Query: {query}")
        return {"error": str(e)}

def handle_jsonrpc_request(data):
    api_key = request.headers.get("X-API-Key")
    if api_key != VALID_API_KEY:
        logger.warning(f"Unauthorized access attempt with API key: {api_key}")
        return json.dumps({"jsonrpc": "2.0", "error": {"code": -32001, "message": "Unauthorized: Invalid API key"}, "id": None})

    request = json.loads(data)
    if request.get("jsonrpc") != "2.0" or "method" not in request:
        logger.warning(f"Invalid JSON-RPC request: {data}")
        return json.dumps({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": request.get("id")})

    method = request["method"]
    params = request.get("params", {})
    request_id = request.get("id")

    if method == "run_redshift_query":
        query = params.get("query")
        if not query:
            logger.warning(f"Missing query parameter in request: {data}")
            return json.dumps({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Missing query parameter"}, "id": request_id})
        result = run_redshift_query(query)
        return json.dumps({"jsonrpc": "2.0", "result": result, "id": request_id})
    else:
        logger.warning(f"Method not found: {method}")
        return json.dumps({"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": request_id})

@app.route("/mcp", methods=["POST"])
def mcp_endpoint():
    def event_stream():
        if request.data:
            response = handle_jsonrpc_request(request.data)
            yield f"data: {response}\n\n"
    
    return Response(event_stream(), mimetype="text/event-stream")

if __name__ == "__main__":
    logger.info("Starting MCP server...")
    run_simple("0.0.0.0", 8080, app, threaded=True)
