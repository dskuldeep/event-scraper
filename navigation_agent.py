import uuid
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from constants import APP_NAME, USER_ID, MODEL
from prompts import NAVIGATION_AGENT_PROMPT

class NavigationAgent:
    def __init__(self):
        self.agent = Agent(
            name="navigation_agent",
            model=MODEL,
            description="Agent to decide navigation or extraction",
            instruction=NAVIGATION_AGENT_PROMPT
        )
        self.session_service = InMemorySessionService()
        self.session_id = str(uuid.uuid4())
        self.session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=self.session_id
        )
        self.runner = Runner(
            agent=self.agent,
            app_name=APP_NAME,
            session_service=self.session_service
        )
    def decide(self, url: str, page_title: str, text_content: str, links: list[str]) -> dict:
        prompt = f"""
        You are a navigation agent for event crawling.
        URL: {url}
        Title: {page_title}
        
        Content:
        {text_content[:2000]}
        
        Links on page:
        {chr(10).join(links[:20])}
        
        Decide if you should click one or more links to get more event details, or if the page has enough information for extraction.
        Respond with a JSON object:
        {{
            "action": "click" or "extract",
            "links": ["<link1>", "<link2>", ...] (if action is click, else empty list)
        }}
        Only choose links if they are likely to lead to more event details (agenda, details, speakers, register, etc). If you choose links, return the most important ones first.
        """
        content = types.Content(
            role='user',
            parts=[types.Part(text=prompt)]
        )
        response = self.runner.run(
            user_id=USER_ID,
            session_id=self.session_id,
            new_message=content
        )
        for msg in response:
            if msg.is_final_response():
                import re
                import json as pyjson
                match = re.search(r'\{[\s\S]*\}', msg.content.parts[0].text)
                if match:
                    try:
                        result = pyjson.loads(match.group(0))
                        # Backward compatibility: support "link" as well as "links"
                        if "link" in result and "links" not in result:
                            result["links"] = [result["link"]] if result["link"] else []
                        return result
                    except Exception:
                        pass
        return {"action": "extract", "links": []}
