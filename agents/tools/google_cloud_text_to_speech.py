"""Google Cloud Text-to-Speech tool."""
from langchain.agents import Tool

try:  # pragma: no cover - requires google-cloud-texttospeech package
    from langchain_community.tools.google_cloud import GoogleCloudTextToSpeechTool

    google_cloud_text_to_speech_tool = GoogleCloudTextToSpeechTool()
except Exception as e:  # pragma: no cover
    err_msg = str(e)

    def _tts_stub(text: str) -> str:
        return f"Google Cloud Text-to-Speech tool unavailable: {err_msg}"  # noqa: B023

    google_cloud_text_to_speech_tool = Tool(
        name="google_cloud_text_to_speech",
        func=_tts_stub,
        description="Convert text to speech using Google Cloud",
    )

__all__ = ["google_cloud_text_to_speech_tool"]
