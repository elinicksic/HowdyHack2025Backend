from openai import OpenAI
from pydantic import BaseModel
from typing import List
from flask import Flask

app = Flask(__name__)


@app.route("/studysets/list", methods=["GET"])
def root_get():
    return "Hello"


if __name__ == '__main__':
    app.run(debug=True)

client = OpenAI(api_key="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")


class ScrollTopic(BaseModel):
    title: str
    sections: List[str]


class ScrollTopicResponse(BaseModel):
    topics: List[ScrollTopic]


response = client.responses.parse(
    model="gpt-5-mini",
    input=[
        {"role": "system",
            "content": "You are to take the information the user gives and break it down into individual learning topics and sections. The sections should be a simple title of the section. For example: \"Strings\". The topic consistents of several topics. Topic titles should also be simple For example: \"Data types\". You should only include material, not study methods."},
        {
            "role": "user",
            "content": "I want to memorize common polyatomic ions and their charges.",
        },
    ],
    text_format=ScrollTopicResponse)

for i, topic in enumerate(response.output_parsed.topics):
    print(f"Topic {i + 1} - {topic.title}")
    for j, section in enumerate(topic.sections):
        print(f"\t{i+1}.{j+1} - {section}")

feed = []


class VideoScript(BaseModel):
    voiceover: str
    video_prompt: str


response = client.responses.parse(
    model="gpt-5-mini",
    input=[
        {"role": "system", "content": "Generate"},
        {
            "role": "user",
            "content": "Teach variables in python",
        },
    ],
    text_format=VideoScript,
)

video = client.videos.create(
    prompt=response.output_parsed.video_prompt,
)
