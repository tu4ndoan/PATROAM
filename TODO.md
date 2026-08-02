# TO-DO
Do not hard code anything, use LLM to detect and recognize my command

## Fix
- hide the moving dots but show when node is selected
- also let me zoom out further so I can see the whole view
- also let me drag to move in the graph (middle mouse)
- remove unused files, folders, refactor code, update README.md
## Planner Agent:
- connected to clickup and slack
- when I tell it to create a project (app, website, ...), it will ask me steps by steps, consult what should be done, what won't work, verify what I want done, and how to get there, create a clear detailed road map, plan, big tasks, subtasks, checkboxes, backlogs
- it will create a project and a README.md for that project and then index that project's README into the knowledge graph under the "Projects" node, with all the informations, my choices, project descriptions,...
- back up the knowledge graph
- on launch, take a look and tell me the project's progress, where I left the previous session, what to do next, am I on schedule,...

## Note taker:
- create a "New Note" function that pops up a window where I can take note and save to a file in the "Notes" folder, then inject it into the knowledge graph also
- on lauching patroam, he will take a look at all my Notes and make some suggestions such as "what to do" "what to look at" "what bug should be fixed"
- take a look at my Notes to see the connections, and make suggestions, for example I have a note says "go to gym this weekend" and a note says "dating with gf on saturday", he will suggest me that there might be a conflict in schedule,...

## Mail?
- look at my mailbox and tell me what's important, anyone waiting my reply, any news, ads, promotions that I should take a look
- 

## Greeting/Briefing
Patroam would treat each session like a professional daily briefing rather than a generic chatbot conversation.

The greeting would have three layers:

A. Executive Summary

A quick snapshot of what matters today, for example:

"Welcome back, sir.
Since our last session:
- you've had 3 more Fab sales which pushed the total sale over 1000 dollars
- there are a few complains about "high shipping fee" and "waited too long for delivery" on Slack channel "HANABIE customers feedback" -> link: go to message. 
- you were working on task "Unreal combat prototype" - list: "Unreal Combat" - space: "General"
Mission status: 3 active projects, 2 pending decisions, 1 urgent issue.
Today's recommended focus: Finish the Unreal combat prototype before switching contexts.

Some news on topics you're interested in:
1. blabblabla
2. blablabla "


B. Personalized Dashboard (only show this on the conversation chat, do not speak out loud)

A structured overview of everything you're responsible for, for example:
"
━━━━━━━━━━━━━━
Daily Briefing
━━━━━━━━━━━━━━

🎯 TOP PRIORITIES
1. Finish combat animation state machine
2. Review returned orders
3. Improve agent memory retrieval

📈 BUSINESS:
• Orders today: 23
• Pending returns: 12
• High-priority customers: 3

🎮 GAME DEVELOPMENT
• Current project: Online RPG
• Current milestone: Combat prototype
• Days until demo target: 48

📰 NEWS
• Unreal Engine: New optimization article
• AI: New open-source reasoning model released
• Vietnam economy: Relevant e-commerce update

📝 NOTES & REMINDERS
• Contact shipping partner regarding failed deliveries
• Create portfolio screenshots
• Draft YouTube content idea

⚡ RECOMMENDED NEXT ACTION
→ Open Unreal project and finish animation blending task"

C. Conversational Opening
Then transition naturally into work, for example:
"Want me to put on your "Focus" playlist?" (if I say yes, open spotify and my focus playlist)


patroam should be:
Action-oriented rather than chat-oriented
Acts like a Chief of Staff + Project Manager + Research Assistant
Always remembers context
Always proposes the next best action

