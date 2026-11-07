# IMPORTS
import os
import re
import uuid
from pathlib import Path
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    flash
)
from werkzeug.utils import secure_filename
from PyPDF2 import PdfReader
from langchain_community.vectorstores import FAISS
from langchain_ollama import ChatOllama
from langchain_classic.chains import ConversationalRetrievalChain
from langchain_classic.memory import ConversationBufferMemory
from langchain_text_splitters import CharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.messages import SystemMessage, HumanMessage
# CONFIGURATION
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")
DATA_DIR = Path("__data__")
DATA_DIR.mkdir(exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf"}
start_greeting = ["hi", "hello"]
end_greeting = ["bye"]
way_greeting = ["who are you?"]
# MODELS
embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)

ollama_client = ChatOllama(
    model="qwen2.5:3b",
    temperature=0.4
)
vectorstore = None
conversation_chain = None
rubric_text = ""
def allowed_file(filename):
    """Check whether the uploaded file is a PDF."""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )

# PDF TEXT EXTRACTION
def get_pdf_text(pdf_docs):
    """Extract text from uploaded PDF documents."""

    text = ""

    for pdf in pdf_docs:

        if not pdf or not pdf.filename:
            continue

        if not allowed_file(pdf.filename):
            continue

        pdf_reader = PdfReader(pdf.stream)

        pdf_text = ""

        for page in pdf_reader.pages:
            page_text = page.extract_text() or ""
            pdf_text += page_text + "\n"

        if not pdf_text.strip():
            continue

        text += pdf_text + "\n"

        # Save extracted text using a safe, unique filename.
        filename = secure_filename(pdf.filename)
        unique_filename = f"{uuid.uuid4().hex}_{filename}.txt"

        text_file_path = DATA_DIR / unique_filename

        with open(text_file_path, "w", encoding="utf-8") as file:
            file.write(pdf_text)

    return text

# TEXT CHUNKING
def get_text_chunks(text):

    text_splitter = CharacterTextSplitter(
        separator="\n",
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len
    )

    chunks = text_splitter.split_text(text)

    return chunks

# VECTOR STORE
def get_vectorstore(text_chunks):

    if not text_chunks:
        raise ValueError("No text chunks were generated from the PDFs.")

    vectorstore = FAISS.from_texts(
        texts=text_chunks,
        embedding=embeddings
    )

    return vectorstore

# CONVERSATIONAL RETRIEVAL CHAIN
def get_conversational_chain(vectorstore):

    memory = ConversationBufferMemory(
        memory_key="chat_history",
        return_messages=True,
        output_key="answer"
    )

    conversation_chain = ConversationalRetrievalChain.from_llm(
        llm=ollama_client,
        retriever=vectorstore.as_retriever(),
        memory=memory,
        return_source_documents=False
    )

    return conversation_chain
# ESSAY GRADING
def grade_essay(essay):

    if not essay or not essay.strip():
        raise ValueError("The essay is empty.")

    if not rubric_text.strip():
        raise ValueError("Please provide an essay grading rubric first.")

    messages = [
        SystemMessage(
            content=(
                "You are an academic essay grading assistant. "
                "Carefully evaluate the essay using the supplied rubric. "
                "Explain the marks awarded, identify strengths and weaknesses, "
                "and provide constructive feedback. "
                "Respond in English only.\n\n"
                "GRADING RUBRIC:\n"
                + rubric_text
            )
        ),

        HumanMessage(
            content="ESSAY:\n" + essay
        )
    ]

    response = ollama_client.invoke(messages)

    data = response.content

    # Convert newlines to HTML line breaks for display.
    data = str(data).replace("\n", "<br>")

    return data
# PDF EXTRACTION FOR ESSAY GRADING
def extract_text_from_pdf(pdf_file):

    pdf_reader = PdfReader(pdf_file.stream)

    text = ""

    for page in pdf_reader.pages:
        text += (page.extract_text() or "") + "\n"

    return text
# HOME ROUTE
@app.route("/")
def home():

    return render_template("new_home.html")

# PROCESS PDF DOCUMENTS
@app.route("/process", methods=["POST"])
def process_documents():

    global vectorstore, conversation_chain

    pdf_docs = request.files.getlist("pdf_docs")

    # Remove empty file selections.
    pdf_docs = [
        pdf for pdf in pdf_docs
        if pdf and pdf.filename
    ]

    if not pdf_docs:
        flash("Please upload at least one PDF document.")
        return redirect("/")

    if any(not allowed_file(pdf.filename) for pdf in pdf_docs):
        flash("Only PDF files are allowed.")
        return redirect("/")

    try:

        raw_text = get_pdf_text(pdf_docs)

        if not raw_text.strip():
            flash("No readable text was found in the uploaded PDFs.")
            return redirect("/")

        text_chunks = get_text_chunks(raw_text)

        vectorstore = get_vectorstore(text_chunks)

        conversation_chain = get_conversational_chain(vectorstore)

        return redirect("/chat")

    except Exception as error:

        app.logger.exception("PDF processing failed")
        flash(f"Error processing documents: {error}")

        return redirect("/")

# CHAT ROUTE
@app.route("/chat", methods=["GET", "POST"])
def chat():

    global conversation_chain

    if conversation_chain is None:
        flash("Please upload and process PDF documents first.")
        return redirect("/")

    try:

        if request.method == "POST":

            user_question = request.form.get("user_question", "").strip()

            if user_question:

                response = conversation_chain.invoke({
                    "question": user_question
                })

        # Retrieve the conversation history from memory.
        chat_history = (
            conversation_chain.memory.chat_memory.messages
        )

        return render_template(
            "new_chat.html",
            chat_history=chat_history
        )

    except Exception as error:

        app.logger.exception("Chat request failed")
        flash(f"An error occurred during the conversation: {error}")

        return redirect("/chat")
# PDF CHAT PAGE
@app.route("/pdf_chat", methods=["GET", "POST"])
def pdf_chat():

    return render_template("new_pdf_chat.html")

# ESSAY GRADING ROUTE
@app.route("/essay_grading", methods=["GET", "POST"])
def essay_grading():

    global rubric_text

    result = None
    input_text = ""

    if request.method == "POST":

        # Check whether the user submitted a rubric.
        submitted_rubric = request.form.get("essay_rubric", "").strip()

        if submitted_rubric:

            rubric_text = submitted_rubric

            flash("Essay grading rubric saved.")

            return render_template(
                "new_essay_grading.html",
                result=None,
                input_text=""
            )

        try:

            # Check whether the user uploaded a PDF essay.
            pdf_file = request.files.get("file")

            if pdf_file and pdf_file.filename:

                if not allowed_file(pdf_file.filename):
                    raise ValueError("Please upload a PDF file.")

                input_text = extract_text_from_pdf(pdf_file)

            else:

                input_text = request.form.get("essay_text", "").strip()

            if not input_text.strip():
                raise ValueError("Please provide an essay or upload a PDF.")

            result = grade_essay(input_text)

        except Exception as error:

            app.logger.exception("Essay grading failed")
            flash(str(error))

    return render_template(
        "new_essay_grading.html",
        result=result,
        input_text=input_text
    )

# ESSAY RUBRIC PAGE
@app.route("/essay_rubric", methods=["GET", "POST"])
def essay_rubric():

    return render_template("new_essay_rubric.html")
# RUN FLASK APPLICATION
if __name__ == "__main__":

    app.run(
        debug=True
    )