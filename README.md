# Obsidian-Anki AI Integration Workflow

This is a Personal Knowledge Management (PKM) automation workflow project based on Python and Large Language Models (Google Gemini). It deeply integrates Obsidian, Anki, and a Multi-Agent System to provide an all-in-one learning solution—from daily note-taking and atomic note splitting to automated flashcard generation, spaced repetition, intelligent Q&A, and homework solving.

## ✨ Core Features

*   **🔄 Two-way Synchronization (`sync.py`)**
    *   Synchronizes cards between Obsidian (Atomic Notes folder) and Anki.
    *   Relies on AnkiConnect, using Obsidian Advanced URI to establish back-links, making it easy to jump back to the original note during reviews.
*   **🧠 Daily Note Auto-Splitting (`daily_to_atomic.py`)**
    *   Integrates with the Gemini LLM to automatically scan Obsidian Daily Notes.
    *   Uses AI to extract and refine long diary entries, automatically splitting them into clear, concise "Atomic Notes".
*   **🤖 Multi-Agent Routing System (`router.py` & `agents/`)**
    *   An extensible AI agent dispatch system that dynamically loads various capability plugins from the `agents/` directory.
    *   Built-in capabilities include (but are not limited to): PubMed academic literature search, note tagging (`tag.py`), content revision (`revise.py`), reading suggestions (`reading_suggestions.py`), etc.
*   **📚 Intelligent Homework Solver (`solve_hw.py`)**
    *   Supports reading PDF documents (via PyMuPDF) or text content.
    *   Utilizes the Gemini API to analyze homework questions and provide highly concise and accurate mathematical or subject-specific answers.

## ⚙️ Configuration & Installation

1.  **Clone the project**
    ```bash
    git clone https://github.com/Mifune-Shioriko/obsidian_anki_workflow.git
    cd obsidian_anki_workflow
    ```

2.  **Install dependencies**
    Python 3.10+ is recommended. Install the required dependencies:
    ```bash
    pip install google-genai pymupdf requests markdown python-dotenv
    ```
    *(Note: For Anki integration, ensure AnkiConnect is installed in Anki, and keep Anki running in the background)*

3.  **Configure environment variables**
    Create a `.env` file in the project root (refer to `.env.example`) and fill in the key configurations:
    ```ini
    GOOGLE_API_KEY="your_gemini_api_key_here"
    VAULT_DIR="/path/to/your/obsidian/vault"
    ANKI_URL="http://127.0.0.1:8765"
    ANKI_DECK_NAME="Obsidian"
    ANKI_NOTE_TYPE="Obsidian"
    OBSIDIAN_VAULT_NAME="my_obsidian_notes"
    ```

## 🚀 Common Commands

*   **Sync to Anki:** `python sync.py`
*   **Process Daily Notes:** `python daily_to_atomic.py`
*   **Solve Homework:** `python solve_hw.py [args]`
*   **Call a specific Agent:** `python router.py` (Depends on your specific configuration)

## 📁 Directory Structure

*   `agents/`: Contains various AI agent scripts (e.g., `pubmed.py`, `file.py`).
*   `anki_card_templates/`: HTML and CSS styling files for Anki cards.
*   `agent_tools.py`: Provides common tool wrappers (e.g., web search capabilities) for the agents.
*   `utils.py`: Common utility functions library for the project.
