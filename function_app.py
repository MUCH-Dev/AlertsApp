import azure.functions as func
import base64
import json
import datetime
import pyodbc
from azure.identity import DefaultAzureCredential

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


def get_caller_email(req: func.HttpRequest):
    principal_header = req.headers.get("X-MS-CLIENT-PRINCIPAL")
    if not principal_header:
        return None
    decoded = base64.b64decode(principal_header)
    principal = json.loads(decoded)
    claims = {c["typ"]: c["val"] for c in principal.get("claims", [])}
    return (
        claims.get("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn")
        or claims.get("preferred_username")
        or claims.get("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name")
    )


@app.route(route="whoami", methods=["GET"])
def whoami(req: func.HttpRequest) -> func.HttpResponse:
    email = get_caller_email(req)
    if not email:
        return func.HttpResponse(
            json.dumps({"error": "No principal header found — Easy Auth may not be enforcing on this route"}),
            status_code=401,
            mimetype="application/json"
        )
    return func.HttpResponse(json.dumps({"email": email}, indent=2), mimetype="application/json")


SQL_SERVER = "alerts-sql-server.database.windows.net"
SQL_DATABASE = "alerts-db"

def get_db_connection(upn: str = None):
    credential = DefaultAzureCredential()
    token = credential.get_token("https://database.windows.net/.default")
    token_bytes = token.token.encode("utf-16-le")
    token_struct = len(token_bytes).to_bytes(4, byteorder="little") + token_bytes

    conn_str = (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server=tcp:{SQL_SERVER},1433;"
        f"Database={SQL_DATABASE};"
        f"Encrypt=yes;TrustServerCertificate=no;"
    )

    SQL_COPT_SS_ACCESS_TOKEN = 1256
    conn = pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})

    # Database-enforced RLS: stamp the SWA-authenticated UPN onto the session.
    # The rls.fn_client_access predicate filters dbo.Alerts by this value
    # (fail-closed: no stamp + shared function identity = no rows).
    # @read_only=1 so nothing later in the session can overwrite it.
    if upn:
        cur = conn.cursor()
        cur.execute(
            "EXEC sp_set_session_context @key=N'upn', @value=?, @read_only=1;",
            upn.lower(),
        )
    return conn


@app.route(route="vendors", methods=["GET"])
def get_vendors(req: func.HttpRequest) -> func.HttpResponse:
    email = get_caller_email(req)
    if not email:
        return func.HttpResponse(
            json.dumps({"error": "Not authenticated"}),
            status_code=401,
            mimetype="application/json"
        )
    try:
        conn = get_db_connection(email)
        cursor = conn.cursor()
        cursor.execute("SELECT vendor FROM DistinctVendors ORDER BY vendor ASC")
        vendors = [row[0] for row in cursor.fetchall()]
        conn.close()
        return func.HttpResponse(json.dumps(vendors), mimetype="application/json")
    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")


def json_safe(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


@app.route(route="alerts", methods=["GET"])
def get_alerts(req: func.HttpRequest) -> func.HttpResponse:
    email = get_caller_email(req)
    if not email:
        return func.HttpResponse(
            json.dumps({"error": "Not authenticated"}),
            status_code=401,
            mimetype="application/json"
        )

    try:
        conn = get_db_connection(email)
        cursor = conn.cursor()

        cursor.execute("SELECT client_code FROM Users WHERE v5_user = ?", email.lower())
        rls_codes = [row[0] for row in cursor.fetchall()]

        if not rls_codes:
            conn.close()
            return func.HttpResponse(json.dumps([]), mimetype="application/json")

        placeholders = ",".join("?" for _ in rls_codes)
        query = f"""
            SELECT id, client_code, client_name, alert_type, vendor, account_number,
                   account_name, tax_id, billing_period, payment_method,
                   account_instructions, latest_account_note, alert_notes, entered, bill_pulled, snoozed_today,
                   last_bill_date, last_bill_due_date, assigned_to, assigned_at, v5_account_id,
                   days_since_last_bill, days_alerted
            FROM Alerts
            WHERE load_date = CAST(GETDATE() AS DATE)
              AND client_code IN ({placeholders})
        """
        params = list(rls_codes)

        status = req.params.get("status")
        if status == "Active":
            query += " AND entered = 0 AND snoozed_today = 0"
        elif status == "Entered":
            query += " AND entered = 1"
        elif status == "Critical":
            query += " AND entered = 0 AND days_since_last_bill >= 65 AND snoozed_today = 0"
        elif status == "Bill Pulled":
            query += " AND entered = 0 AND bill_pulled = 1 AND snoozed_today = 0"
        elif status == "Snoozed":
            query += " AND snoozed_today = 1"
        else:
            query += " AND snoozed_today = 0"

        client_search = req.params.get("client_code_search")
        if client_search:
            query += " AND client_code LIKE ?"
            params.append(client_search + "%")

        vendor_param = req.params.get("vendor")
        if vendor_param:
            vendor_list = [v.strip() for v in vendor_param.split(",") if v.strip()]
            if vendor_list:
                vendor_placeholders = ",".join("?" for _ in vendor_list)
                query += f" AND vendor IN ({vendor_placeholders})"
                params.extend(vendor_list)

        query += " ORDER BY client_code ASC, last_bill_date ASC"

        cursor.execute(query, params)
        columns = [col[0] for col in cursor.description]
        rows = [
            {col: json_safe(val) for col, val in zip(columns, row)}
            for row in cursor.fetchall()
        ]
        conn.close()

        return func.HttpResponse(json.dumps(rows), mimetype="application/json")

    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")


@app.route(route="metrics", methods=["GET"])
def get_metrics(req: func.HttpRequest) -> func.HttpResponse:
    email = get_caller_email(req)
    if not email:
        return func.HttpResponse(
            json.dumps({"error": "Not authenticated"}),
            status_code=401,
            mimetype="application/json"
        )

    try:
        conn = get_db_connection(email)
        cursor = conn.cursor()

        cursor.execute("SELECT client_code FROM Users WHERE v5_user = ?", email.lower())
        rls_codes = [row[0] for row in cursor.fetchall()]

        if not rls_codes:
            conn.close()
            return func.HttpResponse(
                json.dumps({"pending_count": 0, "pulled_count": 0, "entered_count": 0, "total_count": 0}),
                mimetype="application/json"
            )

        placeholders = ",".join("?" for _ in rls_codes)
        query = f"""
            SELECT
                SUM(CASE WHEN entered = 0 AND bill_pulled = 0 AND snoozed_today = 0 THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN entered = 0 AND days_since_last_bill >= 65 AND snoozed_today = 0 THEN 1 ELSE 0 END) AS critical_count,
                SUM(CASE WHEN entered = 1 THEN 1 ELSE 0 END) AS entered_count,
                COUNT(*) AS total_count
            FROM Alerts
            WHERE load_date = CAST(GETDATE() AS DATE)
              AND client_code IN ({placeholders})
        """
        cursor.execute(query, rls_codes)
        row = cursor.fetchone()
        conn.close()

        return func.HttpResponse(
            json.dumps({
                "pending_count": row.pending_count or 0,
                "critical_count": row.critical_count or 0,
                "entered_count": row.entered_count or 0,
                "total_count": row.total_count or 0
            }),
            mimetype="application/json"
        )

    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")


ALLOWED_PATCH_FIELDS = {"alert_notes", "assigned_to", "assigned_at", "bill_pulled", "snoozed_today"}

@app.route(route="alerts/{id}", methods=["PATCH"])
def patch_alert(req: func.HttpRequest) -> func.HttpResponse:
    email = get_caller_email(req)
    if not email:
        return func.HttpResponse(
            json.dumps({"error": "Not authenticated"}),
            status_code=401,
            mimetype="application/json"
        )

    alert_id = req.route_params.get("id")

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid or missing JSON body"}),
            status_code=400,
            mimetype="application/json"
        )

    update_fields = {k: v for k, v in body.items() if k in ALLOWED_PATCH_FIELDS}
    if not update_fields:
        return func.HttpResponse(
            json.dumps({"error": f"No valid fields to update. Allowed: {sorted(ALLOWED_PATCH_FIELDS)}"}),
            status_code=400,
            mimetype="application/json"
        )

    try:
        conn = get_db_connection(email)
        cursor = conn.cursor()

        cursor.execute("SELECT client_code FROM Users WHERE v5_user = ?", email.lower())
        rls_codes = [row[0] for row in cursor.fetchall()]

        if not rls_codes:
            conn.close()
            return func.HttpResponse(
                json.dumps({"error": "No accessible clients for this user"}),
                status_code=403,
                mimetype="application/json"
            )

        if update_fields.get("snoozed_today"):
            check_placeholders = ",".join("?" for _ in rls_codes)
            cursor.execute(
                f"""
                    SELECT entered, days_since_last_bill FROM Alerts
                    WHERE id = ? AND client_code IN ({check_placeholders})
                """,
                [alert_id] + rls_codes
            )
            target = cursor.fetchone()
            if (target and target.entered == 0
                    and target.days_since_last_bill is not None
                    and target.days_since_last_bill >= 65):
                conn.close()
                return func.HttpResponse(
                    json.dumps({"error": "Cannot snooze a Critical alert."}),
                    status_code=400,
                    mimetype="application/json"
                )

        # server-side author stamp: whoever writes/edits the note is recorded
        # (notes_author is intentionally NOT client-patchable) — used by the
        # nightly V5 write-back to append the '*<v5 user id>' suffix.
        if "alert_notes" in update_fields:
            update_fields["notes_author"] = email.lower()

        set_clause = ", ".join(f"{field} = ?" for field in update_fields)
        placeholders = ",".join("?" for _ in rls_codes)
        params = list(update_fields.values()) + [alert_id] + rls_codes

        query = f"""
            UPDATE Alerts
            SET {set_clause}
            WHERE id = ?
              AND client_code IN ({placeholders})
        """
        cursor.execute(query, params)
        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()

        if rows_affected == 0:
            return func.HttpResponse(
                json.dumps({"error": "Alert not found or not accessible"}),
                status_code=404,
                mimetype="application/json"
            )

        return func.HttpResponse(
            json.dumps({"success": True, "id": alert_id, "updated_fields": list(update_fields.keys())}),
            mimetype="application/json"
        )

    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")


@app.route(route="team-members", methods=["GET"])
def get_team_members(req: func.HttpRequest) -> func.HttpResponse:
    email = get_caller_email(req)
    if not email:
        return func.HttpResponse(
            json.dumps({"error": "Not authenticated"}),
            status_code=401,
            mimetype="application/json"
        )

    try:
        conn = get_db_connection(email)
        cursor = conn.cursor()

        cursor.execute("SELECT client_code FROM Users WHERE v5_user = ?", email.lower())
        rls_codes = [row[0] for row in cursor.fetchall()]

        if not rls_codes:
            conn.close()
            return func.HttpResponse(json.dumps([]), mimetype="application/json")

        placeholders = ",".join("?" for _ in rls_codes)
        query = f"""
            SELECT DISTINCT v5_user
            FROM Users
            WHERE client_code IN ({placeholders})
              AND v5_user LIKE '%@muc-corp.com'
            ORDER BY v5_user ASC
        """
        cursor.execute(query, rls_codes)
        members = [row[0] for row in cursor.fetchall()]
        conn.close()

        return func.HttpResponse(json.dumps(members), mimetype="application/json")

    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")
