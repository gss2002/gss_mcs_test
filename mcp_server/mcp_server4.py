import json
import boto3
import os
import logging
import redshift_connector
from flask import Flask, Response, request, jsonify, stream_with_context  # Correct import
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

# AWS clients
redshift_client = boto3.client('redshift', region_name='us-east-1')
sts_client = boto3.client('sts', region_name='us-east-1')

# Redshift configuration from environment variables
CLUSTER_ID = os.getenv("REDSHIFT_CLUSTER_ID", "<your-redshift-cluster-id>")
DATABASE = os.getenv("REDSHIFT_DATABASE", "<your-database-name>")
ROLE_ARN = os.getenv("REDSHIFT_ROLE_ARN", "<your-iam-role-arn-for-redshift>")

# API key for authentication
VALID_API_KEY = os.getenv("MCP_API_KEY", "your-secure-api-key-here")

# Cache connection globally (for simplicity; consider connection pooling in production)
redshift_conn = None

def get_redshift_connection():
    """Get Redshift connection using assumed role and cluster details."""
    global redshift_conn
    if redshift_conn and not redshift_conn.closed:
        return redshift_conn

    try:
        # Look up cluster details
        cluster_info = redshift_client.describe_clusters(ClusterIdentifier=CLUSTER_ID)['Clusters'][0]
        host = cluster_info['Endpoint']['Address']
        port = cluster_info['Endpoint']['Port']

        # Assume IAM role for temporary credentials
        assumed_role = sts_client.assume_role(
            RoleArn=ROLE_ARN,
            RoleSessionName="MCPRedshiftSession"
        )
        credentials = assumed_role['Credentials']

        # Connect to Redshift using redshift_connector
        redshift_conn = redshift_connector.connect(
            host=host,
            port=port,
            database=DATABASE,
            iam=True,
            access_key_id=credentials['AccessKeyId'],
            secret_access_key=credentials['SecretAccessKey'],
            session_token=credentials['SessionToken'],
            region='us-east-1'
        )
        logger.info(f"Connected to Redshift cluster: {CLUSTER_ID}")
        return redshift_conn
    except Exception as e:
        logger.error(f"Failed to connect to Redshift: {str(e)}")
        raise

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
        conn = get_redshift_connection()
        cursor = conn.cursor()
        cursor.execute(query)
        
        # Fetch column names and results
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        result = [dict(zip(columns, row)) for row in rows]
        
        logger.info(f"Query succeeded: {query} - Returned {len(rows)} rows")
        cursor.close()
        return {"result": result}
    except Exception as e:
        logger.error(f"Query execution error: {str(e)} - Query: {query}")
        return {"error": str(e)}
    finally:
        # Commit to release any locks, but don’t close connection (reused)
        if 'conn' in locals():
            conn.commit()

def handle_jsonrpc_request(data):
    api_key = request.headers.get("X-API-Key")
    if api_key != VALID_API_KEY:
        logger.warning(f"Unauthorized access attempt with API key: {api_key}")
        return json.dumps({"jsonrpc": "2.0", "error": {"code": -32001, "message": "Unauthorized: Invalid API key"}, "id": None})

    request_data = json.loads(data)
    if request_data.get("jsonrpc") != "2.0" or "method" not in request_data:
        logger.warning(f"Invalid JSON-RPC request: {data}")
        return json.dumps({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": request_data.get("id")})

    method = request_data["method"]
    params = request_data.get("params", {})
    request_id = request_data.get("id")

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
    
    # Use stream_with_context to preserve request context
    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")

@app.route("/healthz", methods=["GET"])
def healthz():
    """Health check endpoint for Kubernetes."""
    return jsonify({"status": "healthy"}), 200

if __name__ == "__main__":
    logger.info("Starting MCP server...")
    run_simple("0.0.0.0", 8080, app, threaded=True)
