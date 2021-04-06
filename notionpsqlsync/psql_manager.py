import psycopg2.extras
import psycopg2.extensions

# [{'id': 'IrTb', 'slug': 'tags', 'name': 'Tags', 'type': 'multi_select', 'options': [{'id': '891347ba-30bf-41da-95f9-55e5808b746c', 'color': 'pink', 'value': 'a'}, {'id': '82b81fb2-4bf1-43fc-a252-6b04d636c27f', 'color': 'blue', 'value': 'jello'}, {'id': '90560caf-22ce-48c8-9a20-a4e300ea8992', 'color': 'default', 'value': 'aa'}, {'id': '69403082-f5f8-41cf-aa75-c00993c2e24e', 'color': 'green', 'value': 'test'}]}, {'id': 'sE{Q', 'slug': 'date', 'name': 'Date', 'type': 'date'}, {'id': 'title', 'slug': 'name', 'name': 'Name', 'type': 'title'}]

def lst2pgarr(alist):
    return '{' + ','.join(alist) + '}'

def create_table_str_for_schema(schema, table_name):
    """
    Creates CREATE TABLE and associated index statements for specified notion schema
    """
    create_table = f"create table if not exists {table_name} (\n"

    fields = []
    indices = []
    alterations = []

    fields.append("id serial primary key")
    fields.append("notion_id uuid unique")
    indices.append(f"create index if not exists notion_id_idx on {table_name} (notion_id)")

    for prop in schema:
        prop_name = prop["slug"]
        type = prop["type"]

        if type == "date":
            fields.append(f"{prop_name} timestamp with time zone")
            indices.append(f"create index if not exists {prop_name}_idx on {table_name} ({prop_name})")
            alterations.append(f"alter table {table_name} add column if not exists {prop_name} timestamp with time zone")

            # date field can also have end time
            fields.append(f"{prop_name}_end timestamp with time zone")
            indices.append(f"create index if not exists {prop_name}_end_idx on {table_name} ({prop_name}_end)")
            alterations.append(f"alter table {table_name} add column if not exists {prop_name}_end timestamp with time zone")
        elif type == "created_time" or type == "last_edited_time":
            fields.append(f"{prop_name} timestamp not null")
            indices.append(f"create index if not exists {prop_name}_idx on {table_name} ({prop_name})")
            alterations.append(f"alter table {table_name} add column if not exists {prop_name} timestamp not null")
        elif type == "relation":
            fields.append(f"{prop_name} uuid[]")
            alterations.append(f"alter table {table_name} add column if not exists  {prop_name} uuid[]")
            fields.append(f"{prop_name}_title text[]")
            alterations.append(f"alter table {table_name} add column if not exists  {prop_name}_title text[]")
            indices.append(f"create index if not exists {prop_name}_idx on {table_name} using gin ({prop_name})")
            indices.append(f"create index if not exists {prop_name}_title_idx on {table_name} ({prop_name}_title)")
        elif type == "number":
            fields.append(f"{prop_name} double precision")
            indices.append(f"create index if not exists {prop_name}_idx on {table_name} ({prop_name})")
            alterations.append(f"alter table {table_name} add column if not exists {prop_name} double precision")
        elif type == "checkbox":
            fields.append(f"{prop_name} boolean")
            indices.append(f"create index if not exists {prop_name}_idx on {table_name} ({prop_name})")
            alterations.append(f"alter table {table_name} add column if not exists {prop_name} boolean")
        elif type == "multi_select" or type == "person":
            fields.append(f"{prop_name} text[]")
            indices.append(f"create index if not exists {prop_name}_idx on {table_name} using gin ({prop_name})")
            alterations.append(f"alter table {table_name} add column if not exists {prop_name} text[]")
        elif type == "title" or type == "text" or type == "url" or type == "created_by" or type == "select":
            fields.append(f"{prop_name} text")
            indices.append(f"create index if not exists {prop_name}_idx on {table_name} ({prop_name})")
            alterations.append(f"alter table {table_name} add column if not exists {prop_name} text")
        else:
            raise Exception(f"invalid type {type}")
    
    create_table += ",\n".join(fields)
    create_table += ");"
    
    return [create_table] + alterations + indices

def create_insert_stmt_for_rows(table_name, schema, rows):
    prop_names = ["notion_id"]
    for prop in schema:
        prop_name = prop["slug"]
        type = prop["type"]

        if type == "date":
            prop_names.append(prop_name)
            prop_names.append(f"{prop_name}_end")
        elif type == "relation":
            prop_names.append(prop_name)
            prop_names.append(f"{prop_name}_title")
        else:
            prop_names.append(prop_name)

    values = []
    for row in rows:
        cols = (row.id,)
        for prop in schema:
            prop_name = prop["slug"]
            type = prop["type"]

            value = getattr(row, prop["name"])

            if type == "multi_select":
                if value is None:
                    cols += (None,)
                else:
                    cols += (lst2pgarr(value),)
            elif type == "select":
                if value is None:
                    cols += (None,)
                else:
                    cols += (value,)
            elif type == "date":
                if value is None:
                    cols += (None, None)
                else:
                    cols += (value.start, value.end)
            elif type == "last_edited_time":
                cols += (value,)
            elif type == "relation":
                cols += (lst2pgarr([el.id for el in value if el is not None]), lst2pgarr([el.title or "" for el in value if el is not None]))
            elif type == "person":
                cols += (lst2pgarr([p.email for p in value if p is not None]),)
            elif type == "created_by":
                cols += (value.email,)
            else:
                cols += (value,)

        assert len(cols) == len(prop_names), f"{len(cols)} != {len(prop_names)} {cols} {prop_names}"
        values.append(cols)

    update_stmts = [f"{prop_name} = excluded.{prop_name}" for prop_name in prop_names]

    sql = f"""
        insert into {table_name} ({",".join(prop_names)})
        values %s
        on conflict (notion_id) do update set
            {",".join(update_stmts)}
    """

    return sql, values

class PsqlManager:
    def __init__(self, conn, cv, table_name):
        self._conn = conn
        self._table_name = table_name
        self._cv = cv
        self._schema = cv.collection.get_schema_properties()

    def create_table(self):
        cur = self._conn.cursor()

        # create table creation statement
        stmts = create_table_str_for_schema(self._schema, self._table_name)

        # execute statements
        with self._conn.cursor() as cur:
            for stmt in stmts:
                cur.execute(stmt)

        self._conn.commit()

    def sync_rows(self):
        rows = self._cv.collection.get_rows(limit=5000)
        with self._conn as conn:
            with conn.cursor() as cur:
                stmt, values = create_insert_stmt_for_rows(self._table_name, self._schema, rows)

                # insert values into postgresql
                psycopg2.extras.execute_values(cur, stmt, values, page_size=1000)