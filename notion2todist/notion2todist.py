#!/usr/bin/env python3

from typing import Optional

import os
import logging
import typer

from datetime import datetime
from notion.client import NotionClient
from notion.collection import NotionDate
from todoist.api import TodoistAPI

def main(
    todoist_token: str = typer.Option(None, envvar="TODOIST_TOKEN"),
    notion_token: str = typer.Option(..., envvar="NOTION_TOKEN"),
    notion_view: str = typer.Option(..., envvar="NOTION_VIEW"),
    notion_filter: str = typer.Option(None, envvar="NOTION_FILTER"),
    date_field: str = typer.Option("Date", envvar="DATE_FIELD"),
    updated_at_field: str = typer.Option("UpdatedAt", envvar="UPDATED_AT_FIELD"),
    projects_field: str = typer.Option("Project", envvar="PROJECTS_FIELD")):

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    notion_client = NotionClient(notion_token)
    todoist_client = TodoistAPI(todoist_token)

    todoist_client.sync()

    all_projects = todoist_client.projects.all()
    all_items = todoist_client.items.all()

    logging.info("syncing events from notion to todoist")

    synced_events = []
    project_mappings = {}

    cv = notion_client.get_collection_view(notion_view)
    for row in cv.collection.query():
        logging.info("processing row '%s'", row.id)

        synced_events.append(row.id)

        logger = logging.getLogger(row.id)

        id = row.id
        title = row.title
        date = getattr(row, date_field)
        updatedAt = getattr(row, updated_at_field)
        projects = getattr(row, projects_field)

        event_date = None
        if date:
            event_date = date.end if date.end is not None else date.start

        project = projects[0] if len(projects) > 0 else None
        if not project:
            continue

        logger.info("row data %s", {
            "title": title,
            "date": event_date.isoformat() if event_date is not None else None,
            "updatedAt": updatedAt.isoformat() if updatedAt is not None else None,
            "project": project.title if project is not None else None
        })

        todoist_project = next((p for p in all_projects if p.data["name"] == project.title), None)
        if not todoist_project:
            logger.info("creating new todoist project")
            todoist_project = todoist_client.projects.add(project.title)

        todoist_project_id = todoist_project.data["id"]
        project_mappings[todoist_project_id] = project

        logger.info("todoist project %d", todoist_project_id)

        # check if item with same id alreay exists
        item = next((item for item in all_items if "temp_id" in item.data and item.data["temp_id"] == row.id), None)
        if item is not None:
            item_id = item.data["id"]

            logger.info("found existing event with id %d", item_id)

            completed = row.Completed
            if item.data["date_completed"] is not None:
                date_completed = datetime.strptime(item.data["date_completed"], "%Y-%m-%dT%H:%M:%SZ")

                if date_completed > updatedAt:
                    logger.info("syncing update status from todoist to notion")
                    completed = True
                    row.Completed = True

            logger.info("updating item in todoist")

            todoist_client.items.update(item_id,
                content=row.title,
                due={"string": event_date} if event_date is not None else None,
                project_id=todoist_project_id)

            if completed:
                logger.info("completing item in todoist")
                todoist_client.items.complete(item_id)
            elif not completed and item.data["checked"] == 1:
                logger.info("uncompleting item in todoist")
                todoist_client.items.uncomplete(item_id)
        else:
            logger.info("adding item to todoist")

            item = todoist_client.items.add(row.title, temp_id=row.id,
                due={"string": event_date} if event_date is not None else None,
                checked=row.Completed,
                project_id=todoist_project_id)

        todoist_client.commit()

    logging.info("syncing events from todoist to notion")

    for item in todoist_client.items.all():
        if item.data["checked"] == 1:
            continue

        if "temp_id" in item.data and item.data["temp_id"] in synced_events:
            continue

        item_id = item.data["id"]

        logging.info("creating new row in notion for todoist event %d", item_id)
        logger = logging.getLogger(str(item_id))

        row = cv.collection.add_row()
        row.title = item.data["content"]

        if item.data["project_id"] in project_mappings:
            project = project_mappings[item.data["project_id"]]

            logger.info("project found %s", project.title) 

            row.project = [project]

        if "due" in item.data:
            item_due = item.data["due"]

            logger.info("parsing item date %s", item_due)

            try:
                due_date = datetime.strptime(item_due["date"], "%Y-%m-%dT%H:%M:%S")
            except:
                due_date = datetime.strptime(item_due["date"], "%Y-%m-%d")
            
            setattr(row, date_field, NotionDate(due_date,
                timezone = item_due["timezone"] if "timezone" in item_due else None))

        # recreate event with updated temp_id
        item.data.update({"temp_id": row.id}) 
        todoist_client.items.delete(item_id)
        todoist_client.items.add(**item.data)

        todoist_client.commit()

if __name__ == "__main__":
    typer.run(main)
