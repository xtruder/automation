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
    notion_tasks_view: str = typer.Option(..., envvar="NOTION_TASKS_VIEW"),
    notion_projects_view: str = typer.Option(..., envvar="NOTION_PROJECTS_VIEW"),
    notion_todoist_id_field: str = typer.Option("TodoistId", envvar="TODOIST_ID_FIELD"),
    notion_filter: str = typer.Option(None, envvar="NOTION_FILTER"),
    notion_date_field: str = typer.Option("Date", envvar="DATE_FIELD"),
    notion_completed_field: str = typer.Option("Completed", envvar="COMPLETED_FIELD"),
    notion_updated_at_field: str = typer.Option("UpdatedAt", envvar="UPDATED_AT_FIELD"),
    notion_projects_field: str = typer.Option("Project", envvar="PROJECTS_FIELD")):

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    notion_client = NotionClient(notion_token)
    todoist_client = TodoistAPI(todoist_token)

    logging.info("syncing todoist")

    todoist_client.sync()

    all_projects = todoist_client.projects.all()
    all_items = todoist_client.items.all()

    logging.info("getting notion collection views for projects and tasks")

    notion_projects_view = notion_client.get_collection_view(notion_projects_view) 
    notion_tasks_view = notion_client.get_collection_view(notion_tasks_view) 

    logging.info("fethcing notion projects")

    notion_projects = {}
    for row in notion_projects_view.collection.query():
        notion_projects[row.title] = row

    logging.info("syncing events from notion to todoist")

    synced_items = []

    for row in notion_tasks_view.collection.query():
        logging.info("processing row '%s'", row.id)

        logger = logging.getLogger(row.id)

        id = row.id
        title = row.title
        date = getattr(row, notion_date_field)
        updatedAt = getattr(row, notion_updated_at_field)
        projects = getattr(row, notion_projects_field)
        completed = getattr(row, notion_completed_field)
        todoist_id = getattr(row, notion_todoist_id_field, None)

        if not title:
            logger.info("skipping row with empty title")
            continue

        event_date = None
        if date:
            event_date = date.end if date.end is not None else date.start

        project = projects[0] if len(projects) > 0 else None

        logger.info("row data %s", {
            "title": title,
            "date": event_date.isoformat() if event_date is not None else None,
            "updatedAt": updatedAt.isoformat() if updatedAt is not None else None,
            "project": project.title if project is not None else None,
            "todoist_id": todoist_id
        })

        todoist_project_name = project.title if project is not None else "Inbox"

        # check if project with same name already exists or create a new todoist project
        todoist_project = next((p for p in all_projects if p.data["name"] == todoist_project_name and p.data["is_deleted"] != 1), None)
        if todoist_project is not None:
            todoist_project_id = todoist_project.data["id"]
        else:
            logger.info("creating new todoist project")

            todoist_project = todoist_client.projects.add(todoist_project_name)
            result = todoist_client.commit()
            todoist_project_id = result["projects"][0]["id"]

            logger.info("resyncing todoist projects")

            todoist_client.sync()
            all_projects = todoist_client.projects.all()

        logger.info("todoist project %s", str(todoist_project_id))

        # check if item with same id alreay exists
        item = next((i for i in all_items if todoist_id is not None and str(i.data["id"]) == str(todoist_id)), None)
        if item is not None:
            item_id = item.data["id"]

            logger.info("found existing todoist item with id %d", item_id)

            if item.data["date_completed"] is not None:
                date_completed = datetime.strptime(item.data["date_completed"], "%Y-%m-%dT%H:%M:%SZ")

                if date_completed > updatedAt:
                    logger.info("syncing update status from todoist to notion")
                    completed = True
                    setattr(row, notion_completed_field, True)

            logger.info("updating item in todoist")

            todoist_client.items.update(item_id,
                content=row.title,
                due={"string": event_date} if event_date is not None else None,
                project_id=todoist_project_id)

            if item.data["project_id"] != todoist_project_id:
                logger.info("moving item to new project")
                todoist_client.items.move(item_id, project_id=todoist_project_id)

            if completed and item.data["checked"] != 1:
                logger.info("completing item in todoist")
                todoist_client.items.complete(item_id)
            elif not completed and item.data["checked"] == 1:
                logger.info("uncompleting item in todoist")
                todoist_client.items.uncomplete(item_id)
        else:
            logger.info("adding item to todoist")

            item = todoist_client.items.add(row.title,
                due={"string": event_date} if event_date is not None else None,
                checked=completed,
                project_id=todoist_project_id)

            if completed:
                todoist_client.items.complete(item.temp_id)

        result = todoist_client.commit()

        # if todoist_id has not been set in notion, retrieve it from id mapping
        if todoist_id != result["items"][0]["id"]: 
            todoist_id = result["items"][0]["id"]

            logger.info("updating todoist id mapping in notion")
            setattr(row, notion_todoist_id_field, result["items"][0]["id"])

        synced_items.append(todoist_id)

    logging.info("syncing tasks from todoist to notion")

    todoist_client.sync()
    all_projects = todoist_client.projects.all()
    all_items = todoist_client.items.all()

    for item in all_items:
        item_id = item.data["id"]

        if item.data["checked"] == 1 or item.data["is_deleted"] == 1 or item_id in synced_items:
            continue

        logging.info("creating new row in notion for todoist event %d", item_id)
        logger = logging.getLogger(str(item_id))

        row = notion_tasks_view.collection.add_row()
        row.title = item.data["content"]
        setattr(row, notion_todoist_id_field, item_id)

        # set row project if found in existing project mappings
        todoist_project = next((p for p in all_projects if p.data["id"] == item.data["project_id"]))
        todoist_project_name = todoist_project.data["name"] 
        if todoist_project_name != "Inbox" and todoist_project_name in notion_projects:
            project = notion_projects[todoist_project.data["name"]]

            logger.info("project found %s", project.title) 

            row.project = [project]

        # create new project in notion if project with same name not found
        else:
            logger.info("creating new project in notion %s", todoist_project_name)

            project = notion_projects_view.collection.add_row()
            project.title = todoist_project_name

            logger.info("updating notion task with project")

            row.project = [project]

        # set row date
        if "due" in item.data:
            item_due = item.data["due"]

            if item_due is not None:
                logger.info("parsing item due %s", item_due)

                try:
                    due_date = datetime.strptime(item_due["date"], "%Y-%m-%dT%H:%M:%SZ")
                except:
                    try:
                        due_date = datetime.strptime(item_due["date"], "%Y-%m-%dT%H:%M:%S")
                    except:
                        due_date = datetime.strptime(item_due["date"], "%Y-%m-%d")
                
                setattr(row, date_field, NotionDate(due_date,
                    timezone = item_due["timezone"] if "timezone" in item_due else None))

if __name__ == "__main__":
    typer.run(main)
