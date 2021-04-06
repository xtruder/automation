#!/usr/bin/env python3

from typing import Optional

import typer
import logging
import psycopg2

from notion.client import NotionClient

from psql_manager import PsqlManager

def main(
    notion_token: str = typer.Option(..., envvar="NOTION_TOKEN"),
    notion_tasks_view: str = typer.Option(..., envvar="NOTION_TASKS_VIEW"),
    psql_conn_string: str = typer.Option(..., envvar="PSQL_CONN_STRING"),
    psql_table_name: str = typer.Option(..., envvar="PSQL_TABLE_NAME"),
):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    client = NotionClient(notion_token)

    conn = psycopg2.connect(psql_conn_string)

    cv = client.get_collection_view(notion_tasks_view)
    schema = cv.collection.get_schema_properties()
    manager = PsqlManager(conn, cv, psql_table_name)

    manager.create_table()
    manager.sync_rows()

if __name__ == "__main__":
    typer.run(main)
