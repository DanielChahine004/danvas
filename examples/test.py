import csv
import pycanvas

canvas = pycanvas.Canvas()

# Load CSV
with open(r"C:\Users\h\Desktop\pycanvas\examples\hackathon\hackathon_inventory.csv") as f:
    rows = list(csv.DictReader(f))

numeric_cols = ["stock", "price"]

canvas.show(type(rows))

t = canvas.table(rows, name="inventory", label="Inventory", x=0, y=0)
avg_label = canvas.label("avg", value="Select rows to see averages", label="Selection averages",
                          x=560, y=0)

@t.on_select
def _(indices):
    if not indices:
        avg_label.update("Select rows to see averages")
        return
    selected = [rows[i] for i in indices]
    parts = [f"{len(selected)} rows selected"]
    for col in numeric_cols:
        vals = [float(r[col]) for r in selected if r.get(col) not in (None, "")]
        if vals:
            parts.append(f"{col}:  avg {sum(vals)/len(vals):.2f}")
    avg_label.update("\n".join(parts))

canvas.serve(hot_reload=True)
 