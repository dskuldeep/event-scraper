import streamlit as st
import os
import json
from pathlib import Path
import time
from streamlit_autorefresh import st_autorefresh
import networkx as nx
import matplotlib.pyplot as plt
from collections import defaultdict
from datetime import datetime

# Must be the first Streamlit command
st.set_page_config(page_title="Event Finder Dashboard", layout="wide")

# Get the absolute path to the workspace directory
WORKSPACE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

# Store agent history and fixed positions in session state
if 'agent_history' not in st.session_state:
    st.session_state.agent_history = []
    st.session_state.last_log_time = None
    # Fixed positions for nodes in a triangular layout
    st.session_state.pos = {
        "Search Agent": (0, 1),
        "Navigation Agent": (-1, -0.5),
        "Extraction Agent": (1, -0.5)
    }

def read_log_file(logfile, max_lines=100):
    filepath = WORKSPACE_DIR / logfile
    if not filepath.exists():
        return ["No log file found."]
    with open(filepath, "r") as f:
        lines = f.readlines()
    return lines[-max_lines:]

def read_json_file(filename):
    filepath = WORKSPACE_DIR / filename
    if not filepath.exists():
        return None
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except Exception as e:
        return f"Error reading {filename}: {str(e)}"

def read_events(events_folder="events"):
    events = []
    folder = WORKSPACE_DIR / events_folder
    if not folder.exists():
        return events
    for file in sorted(folder.glob("event*.json"), key=lambda x: int(x.stem.replace("event", ""))):
        try:
            with open(file, "r") as f:
                event = json.load(f)
                events.append(event)
        except Exception:
            continue
    return events

def parse_log_time(line):
    try:
        time_str = line.split(" - ")[0].strip()
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S,%f")
    except:
        return None

def get_agent_action(line):
    if "🔍 Searching for:" in line:
        return "Search Agent", line.split("🔍 Searching for:")[1].strip()
    elif "🌐 Visiting:" in line:
        return "Navigation Agent", line.split("🌐 Visiting:")[1].strip()
    elif "📝 Extracted event:" in line:
        return "Extraction Agent", line.split("📝 Extracted event:")[1].split("from")[0].strip()
    return None, None

def get_current_agent_info(log_lines):
    for line in reversed(log_lines):
        agent, action = get_agent_action(line)
        if agent:
            return agent, action, parse_log_time(line)
    return None, None, None

def create_agent_network(log_lines):
    G = nx.DiGraph()
    agent_interactions = defaultdict(lambda: {"count": 0, "last_action": ""})
    last_agent = None
    last_action = None
    current_agent, current_action, current_time = get_current_agent_info(log_lines)
    
    # Track agent transitions for history
    if current_agent and current_time:
        if not st.session_state.last_log_time or current_time > st.session_state.last_log_time:
            st.session_state.agent_history.append((current_agent, current_action, current_time))
            st.session_state.last_log_time = current_time
    
    # Keep only last 20 transitions
    if len(st.session_state.agent_history) > 20:
        st.session_state.agent_history = st.session_state.agent_history[-20:]
    
    agents = ["Search Agent", "Navigation Agent", "Extraction Agent"]
    node_colors = []
    node_sizes = []
    
    # Add nodes with fixed positions
    for agent in agents:
        G.add_node(agent, pos=st.session_state.pos[agent])
        if agent == current_agent:
            node_colors.append('red')
            node_sizes.append(3000)
        else:
            node_colors.append('lightblue')
            node_sizes.append(2000)
    
    # Process log for transitions
    for line in log_lines:
        agent, action = get_agent_action(line)
        if agent:
            if last_agent and last_agent != agent:
                agent_interactions[(last_agent, agent)]["count"] += 1
                agent_interactions[(last_agent, agent)]["last_action"] = last_action
            last_agent = agent
            last_action = action

    # Add edges with detailed labels
    edge_colors = []
    edge_widths = []
    edge_labels = {}
    
    for (source, target), data in agent_interactions.items():
        G.add_edge(source, target)
        count = data["count"]
        action = data["last_action"]
        
        # Highlight recent transition
        if len(st.session_state.agent_history) >= 2:
            last_source = st.session_state.agent_history[-2][0]
            last_target = st.session_state.agent_history[-1][0]
            if (source, target) == (last_source, last_target):
                edge_colors.append('red')
                edge_widths.append(2.0)
                # Show detailed action on active transition
                edge_labels[(source, target)] = f"{action}\n(calls: {count})"
            else:
                edge_colors.append('gray')
                edge_widths.append(0.5)
                edge_labels[(source, target)] = f"calls: {count}"
        else:
            edge_colors.append('gray')
            edge_widths.append(0.5)
            edge_labels[(source, target)] = f"calls: {count}"

    if not edge_colors:
        edge_colors = ['gray']
        edge_widths = [0.5]

    return G, node_colors, node_sizes, edge_colors, edge_widths, edge_labels

# Refresh every 2 seconds
st_autorefresh(interval=2000, key="datarefresh")

st.title("Event Finder Agent Dashboard")

# Create tabs for different monitoring views
tabs = st.tabs(["Live Logs", "URL Queue", "Crawled Links", "Events", "Agent Visualizer"])

with tabs[0]:
    st.header("Live Agent Activity")
    st.caption("Shows the most recent crawler activity")
    log_content = read_log_file("event_finder.log")
    st.code("".join(log_content), language="text")

with tabs[1]:
    st.header("URL Queue")
    st.caption("Current URLs waiting to be processed")
    queue = read_json_file("url_queue.json")
    if queue:
        if len(queue) == 0:
            st.info("Queue is empty")
        else:
            for url in queue:
                st.text(url)
    else:
        st.warning("Could not read URL queue")

with tabs[2]:
    st.header("Crawled Links")
    st.caption("URLs that have been processed")
    crawled = read_json_file("crawled_links.json")
    if crawled:
        if len(crawled) == 0:
            st.info("No links crawled yet")
        else:
            st.write(f"Total crawled: {len(crawled)}")
            for url in crawled:
                st.text(url)
    else:
        st.warning("Could not read crawled links")

with tabs[3]:
    st.header("Collected Events")
    events = read_events("events")
    if not events:
        st.info("No events collected yet")
    else:
        st.write(f"Total events: {len(events)}")
        for i, event in enumerate(events, 1):
            with st.expander(f"Event {i}: {event.get('event_name', 'Unknown Event')}"):
                st.json(event)

with tabs[4]:
    st.header("Agent Interaction Network")
    st.caption("Live visualization of agent interactions and transitions")
    
    log_content = read_log_file("event_finder.log", max_lines=1000)
    # Get current agent info before creating network
    current_agent, current_action, _ = get_current_agent_info(log_content)
    G, node_colors, node_sizes, edge_colors, edge_widths, edge_labels = create_agent_network(log_content)
    
    # Create visualization with fixed positions
    plt.figure(figsize=(12, 8))
    plt.clf()
    
    agents = ["Search Agent", "Navigation Agent", "Extraction Agent"]
    node_colors = []
    node_sizes = []
    
    # Add nodes with fixed positions
    for agent in agents:
        G.add_node(agent, pos=st.session_state.pos[agent])
        if agent == current_agent:
            node_colors.append('red')
            node_sizes.append(3000)
        else:
            node_colors.append('lightblue')
            node_sizes.append(2000)
    
    # Draw the network with fixed positions
    pos = nx.get_node_attributes(G, 'pos')
    nx.draw(G, pos, 
            with_labels=True,
            node_color=node_colors,
            node_size=node_sizes,
            edge_color=edge_colors,
            width=edge_widths,
            font_size=10,
            font_weight='bold',
            connectionstyle='arc3,rad=0.2')  # Curved edges for better visibility
    
    # Add edge labels with padding
    nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, font_size=8)
    
    # Add some padding around the graph
    plt.margins(0.2)
    
    # Display the plot in Streamlit
    st.pyplot(plt)
    
    # Show current active agent and recent transitions
    if current_agent:
        st.info(f"Currently Active: {current_agent}")
        if current_action:
            st.text(f"Current Action: {current_action}")
    
    # Display recent agent transitions
    st.subheader("Recent Agent Transitions")
    for i in range(len(st.session_state.agent_history)-1, max(-1, len(st.session_state.agent_history)-6), -1):
        agent, action, timestamp = st.session_state.agent_history[i]
        st.text(f"{timestamp.strftime('%H:%M:%S')} - {agent}: {action}")
