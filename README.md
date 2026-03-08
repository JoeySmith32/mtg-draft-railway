# MTG Pick-2 Draft — VS Code Setup

## First Time Setup (do this once)

1. **Open the folder in VS Code**
   - File → Open Folder → select the `mtg-draft` folder

2. **Install the Python extension** (if you haven't already)
   - Click the Extensions icon in the left sidebar (or press `Ctrl+Shift+X`)
   - Search for **Python** by Microsoft and install it

3. **Select your Python interpreter**
   - Press `Ctrl+Shift+P`
   - Type: `Python: Select Interpreter`
   - Choose any Python 3.10+ option from the list

## Running the App

Press **F5** (or go to Run → Start Debugging → select "Run MTG Draft")

That's it! The app will:
- Automatically install Flask and other dependencies on first run
- Start the server
- Open your browser to http://localhost:5000

## Playing the Game

1. On the setup page, enter 4 player names and a card list (one card name per line)
2. Click **Begin the Draft** — the server fetches card images from Scryfall
3. Share each player's unique link (they can be on any device on the same WiFi)
4. Each player picks 2 cards, clicks Lock In, packs pass automatically
5. After 3 rounds, everyone has ~42 cards to build a 40-card deck

## Card List Tips

- One card name per line, Scryfall fuzzy-matches so minor typos are OK
- Recommended: 180 cards (15 per pack × 3 packs × 4 players)
- Minimum: 12 cards (for testing)

## Stopping the Server

Press **Shift+F5** in VS Code, or close the terminal panel.
