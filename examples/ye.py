import os
import pycanvas

canvas = pycanvas.Canvas()

# --- 1. Python logic to list actual files ---
def get_directory_tree(path):
    """Recursively builds a tree structure of the given directory."""
    items = []
    try:
        # Sort so folders appear first, then files alphabetically
        for entry in sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower())):
            # Skip hidden files and __pycache__
            if entry.name.startswith('.') or entry.name == "__pycache__":
                continue
                
            node = {
                "name": entry.name,
                "id": entry.path, # Use absolute path as ID
                "type": "folder" if entry.is_dir() else "file"
            }
            
            if entry.is_dir():
                node["children"] = get_directory_tree(entry.path)
                
            items.append(node)
    except Exception as e:
        print(f"Error scanning {path}: {e}")
        
    return items

# Get the directory of the current python script
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ONE_LEVEL_UP = os.path.dirname(ROOT_DIR)
current_files = get_directory_tree(ONE_LEVEL_UP)

# --- 2. Functional React Component with High Contrast CSS ---
JSX_SOURCE = """
function Component({ canvas, props }) {
  const [expanded, setExpanded] = React.useState([]);
  const [selected, setSelected] = React.useState(null);

  const toggleFolder = (id, name) => {
    const isOpening = !expanded.includes(id);
    setExpanded(prev => isOpening ? [...prev, id] : prev.filter(i => i !== id));
    canvas.send({ event: 'navigate', path: id, open: isOpening });
  };

  const selectFile = (id, name) => {
    setSelected(id);
    canvas.send({ event: 'select', path: id, name: name });
  };

  const TreeItem = ({ item }) => {
    const isFolder = item.type === 'folder';
    const isOpen = expanded.includes(item.id);
    const isSelected = selected === item.id;

    return (
      <li className="tree-item">
        {isFolder ? (
          <>
            <div className="tree-label" onClick={() => toggleFolder(item.id, item.name)}>
              <svg className="icon" style={{display: isOpen ? 'none' : 'block'}} xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 2H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>
              <svg className="icon icon-open" style={{display: isOpen ? 'block' : 'none'}} xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 2H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/><path d="M2 10h20"/></svg>
              <span className="label-text">{item.name}</span>
            </div>
            <div className={`tree-children-wrapper ${isOpen ? 'open' : ''}`}>
              <ul className="tree-children">
                {item.children && item.children.map(child => <TreeItem key={child.id} item={child} />)}
              </ul>
            </div>
          </>
        ) : (
          <div className={`file-item ${isSelected ? 'is-selected' : ''}`} onClick={() => selectFile(item.id, item.name)}>
            <svg className="icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span className="label-text">{item.name}</span>
          </div>
        )}
      </li>
    );
  };

  return (
    <div className="tree-container">
      <style>{`
        .tree-container { 
          width: 100%; 
          height: 100%; 
          padding: 12px; 
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; 
          background: #ffffff; 
        }
        ul { list-style: none; padding: 0; margin: 0; }
        ul ul { 
          margin-left: 12px; 
          padding-left: 12px; 
          border-left: 2px solid #e2e8f0; 
        }
        .tree-item { position: relative; margin-top: 2px; }
        
        /* High Contrast Text */
        .label-text {
          color: #000000;
          font-weight: 500;
          font-size: 14px;
        }

        .tree-label, .file-item { 
          display: flex; 
          align-items: center; 
          gap: 10px; 
          padding: 6px 10px; 
          border-radius: 6px; 
          cursor: pointer; 
          transition: all 0.15s ease;
          user-select: none;
        }

        .tree-label:hover, .file-item:hover { background-color: #f1f5f9; }
        .is-selected { background-color: #e2e8f0 !important; }
        .is-selected .label-text { color: #2563eb; font-weight: 600; }

        /* Darker Icons */
        .icon { width: 18px; height: 18px; color: #475569; flex-shrink: 0; }
        .icon-open { color: #1e293b; }
        .is-selected .icon { color: #2563eb; }

        .tree-children-wrapper { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.2s ease; }
        .tree-children-wrapper.open { grid-template-rows: 1fr; }
        .tree-children { overflow: hidden; }
      `}</style>
      <ul>
        {props.files.map(item => <TreeItem key={item.id} item={item} />)}
      </ul>
    </div>
  );
}
"""

# --- 3. Setup Components ---
browser = canvas.react(JSX_SOURCE, props={"files": current_files}, width=350, height=500, grabable=False, frame=False)
status = canvas.label("status", value=f"Exploring: {ROOT_DIR}", x=380, y=10)

@browser.on("select")
def on_select(msg):
    full_path = msg['path']
    filename = msg['name']
    # You can now do real logic with the file path
    size = os.path.getsize(full_path)
    status.update(f"Selected: {filename} ({size} bytes)")
    print(f"Python processing: {full_path}")

@browser.on("navigate")
def on_nav(msg):
    print(f"Navigating: {msg['path']} (Open: {msg['open']})")

canvas.serve()