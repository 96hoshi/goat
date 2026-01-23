#!/usr/bin/env python3
"""Initialize DuckLake catalog.

This script creates the DuckLake metadata catalog in PostgreSQL if it doesn't exist.
It should be run once before starting services that depend on DuckLake (geoapi, processes).

Environment variables:
    POSTGRES_SERVER: PostgreSQL host (default: db)
    POSTGRES_USER: PostgreSQL user (default: postgres)
    POSTGRES_PASSWORD: PostgreSQL password (required)
    POSTGRES_DB: PostgreSQL database (default: goat)
    DUCKLAKE_DATA_DIR: DuckLake data directory (default: /app/data/ducklake)
    DUCKLAKE_CATALOG_SCHEMA: DuckLake catalog schema (default: ducklake)
    S3_ENDPOINT_URL: S3/MinIO endpoint URL (optional, for S3 storage)
    S3_ACCESS_KEY_ID: S3 access key (optional)
    S3_SECRET_ACCESS_KEY: S3 secret key (optional)
"""

import os
import sys


def main():
    import duckdb

    # Get configuration from environment
    pg_host = os.environ.get("POSTGRES_SERVER", "db")
    pg_user = os.environ.get("POSTGRES_USER", "postgres")
    pg_password = os.environ.get("POSTGRES_PASSWORD")
    pg_db = os.environ.get("POSTGRES_DB", "goat")
    storage_path = os.environ.get("DUCKLAKE_DATA_DIR", "/app/data/ducklake")
    catalog_schema = os.environ.get("DUCKLAKE_CATALOG_SCHEMA", "ducklake")

    if not pg_password:
        print("ERROR: POSTGRES_PASSWORD is required", file=sys.stderr)
        sys.exit(1)

    # Create storage directory if it doesn't exist
    os.makedirs(storage_path, exist_ok=True)

    print("Initializing DuckLake...")
    print(f"  PostgreSQL: {pg_host}/{pg_db}")
    print(f"  Catalog schema: {catalog_schema}")
    print(f"  Data directory: {storage_path}")

    # Connect to DuckDB
    con = duckdb.connect()

    # Install and load required extensions
    print("Installing DuckDB extensions...")
    for ext in ["spatial", "httpfs", "postgres", "ducklake"]:
        con.execute(f"INSTALL {ext}; LOAD {ext};")

    # Configure S3 if endpoint is set (for MinIO)
    s3_endpoint = os.environ.get("S3_ENDPOINT_URL", "")
    if s3_endpoint:
        # Strip protocol for DuckDB
        s3_endpoint_clean = s3_endpoint.replace("http://", "").replace("https://", "")
        s3_access_key = os.environ.get("S3_ACCESS_KEY_ID", "")
        s3_secret_key = os.environ.get("S3_SECRET_ACCESS_KEY", "")
        print(f"  S3 endpoint: {s3_endpoint_clean}")
        con.execute(f"""
            SET s3_endpoint = '{s3_endpoint_clean}';
            SET s3_access_key_id = '{s3_access_key}';
            SET s3_secret_access_key = '{s3_secret_key}';
            SET s3_url_style = 'path';
            SET s3_use_ssl = false;
        """)

    # Build postgres connection string with keepalive settings
    pg_uri = (
        f"host={pg_host} port=5432 user={pg_user} password={pg_password} dbname={pg_db} "
        f"keepalives=1 keepalives_idle=30 keepalives_interval=5 keepalives_count=5"
    )

    # Attach DuckLake (this creates the catalog if it doesn't exist)
    print("Attaching DuckLake catalog...")
    con.execute(f"""
        ATTACH 'ducklake:postgres:{pg_uri}' AS lake (
            DATA_PATH '{storage_path}',
            METADATA_SCHEMA '{catalog_schema}'
        )
    """)

    print("DuckLake initialized successfully!")
    print(f"  Catalog: {catalog_schema}")
    print(f"  Data path: {storage_path}")

    con.close()


if __name__ == "__main__":
    main()
