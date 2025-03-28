import streamlit as st
import requests
import json
import os
from sseclient import SSEClient

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://mcp-redshift-service:8080/mcp")
API_KEY = os.getenv("MCP_API_KEY", "your-secure-api-key-here")

def send_query_to_mcp(query):
    try:
        payload = {
            "jsonrpc": "2.0",
            "method": "run_redshift_query",
            "params": {"query": query},
            "id": 1
        }
        headers = {"X-API-Key": API_KEY}

        response = requests.post(MCP_SERVER_URL, json=payload, headers=headers, stream=True)
        response.raise_for_status()

        client = SSEClient(response)
        for event in client.events():
            data = json.loads(event.data)
            if "result" in data:
                return data["result"]["result"]
            elif "error" in data:
                return {"error": data["error"]}
            else:
                return {"error": "Unexpected response format"}
    except requests.RequestException as e:
        return {"error": f"Failed to connect to MCP server: {str(e)}"}
    except Exception as e:
        return {"error": f"Error processing response: {str(e)}"}

st.title("Redshift Query Executor via MCP")
st.write("Enter a SELECT query to execute against Redshift. Authentication is handled by OIDC.")

query = st.text_area("SQL Query (SELECT only)", "SELECT * FROM sales LIMIT 10", height=100)

if st.button("Run Query"):
    with st.spinner("Executing query..."):
        result = send_query_to_mcp(query)
        
        if isinstance(result, dict) and "error" in result:
            st.error(f"Error: {result['error']}")
        elif result:
            st.write("Query Results:")
            st.table(result)
        else:
            st.warning("No results returned.")

st.markdown("""
### Instructions
1. Enter a valid Redshift SELECT query.
2. Click "Run Query" to execute it via the MCP server.
3. Only SELECT statements are allowed for security.
""")
