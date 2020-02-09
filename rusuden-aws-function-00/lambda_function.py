import json
import email
import mimetypes
import time
import urllib.request
import os
from typing import Optional

import boto3
from botocore.client import BaseClient

EXPECTED_MIMETYPE = 'audio/x-wav'
EXPECTED_MEDIA_FORMAT = 'wav'
EXPECTED_LANGUAGE_CODE = 'ja-JP'

s3_client = boto3.resource('s3')
transcribe_client = boto3.client('transcribe')

def lambda_handler(event, context):
    def main() -> None:
        bucket, s3_object_key = get_target_bucket_name_and_object_key(event)
        mail_data = get_mail_data_from_s3(
            bucket,
            s3_object_key,
            s3_client
        )
        msg_obj = parse_msg_obj(mail_data)
        attach_data, attach_filename, attach_mimetype = parse_attachment(msg_obj)
        if attach_data:
            if attach_mimetype == EXPECTED_MIMETYPE:
                put_audio_data_to_bucket(
                    bucket,
                    attach_data,
                    attach_filename,
                    attach_mimetype,
                    s3_client
                )
                media_file_uri = create_media_file_uri(bucket, attach_filename)
                unique_job_name = create_unique_job_name(context)
                start_transcribe_job(
                    unique_job_name,
                    media_file_uri,
                    EXPECTED_MEDIA_FORMAT,
                    EXPECTED_LANGUAGE_CODE,
                    transcribe_client
                )
                job_status = wait_for_job_completion(
                    unique_job_name,
                    transcribe_client
                )
                if job_status == 'COMPLETED':
                    transcript_file_uri = get_transcript_file_uri(
                        unique_job_name,
                        transcribe_client
                    )
                    transcript_json = get_transcript_json(transcript_file_uri)
                    transcript_text = parse_transcript_text(transcript_json)
                    (
                        trello_api_endpoint,
                        trello_key,
                        trello_token,
                        trello_idlist_ready
                    ) = parse_environment_values()
                    card_title = create_card_title(attach_filename)
                    mail_content = parse_mail_content(msg_obj)
                    card_discript = create_card_discript(transcript_text, mail_content)
                    create_trello_card(
                        trello_key,
                        trello_token,
                        trello_idlist_ready,
                        card_title,
                        card_discript,
                        trello_api_endpoint
                    )
                else:
                    print('transcribe job terminated in \'FAILED\' status.')
            else:
                print(
                    "unexpected mimetype. Expected:\'{0}\' Actual:\'{1}\'" \
                        .format(EXPECTED_MIMETYPE, attach_mimetype)
                )
        else:
            print('no attachment.')

    # eventからメールデータが格納されたバケット名とオブジェクトキーを取得する
    def get_target_bucket_name_and_object_key(event: dict) -> str:
        s3record = event['Records'][0]['s3']
        return(
            s3record['bucket']['name'],
            s3record['object']['key']
        )

    # S3からメールデータを取得する
    def get_mail_data_from_s3(bucket: str, key: str, s3: BaseClient) -> object:
        return s3.meta.client.get_object(
            Bucket=bucket,
            Key=key
        )

    # メールデータからメッセージオブジェクトを取得する
    def parse_msg_obj(mail_data: object) -> object:
        return email.message_from_string(
            mail_data['Body'].read().decode('utf-8')
        )

    # メッセージオブジェクトに添付された音声ファイルを取得する
    def parse_attachment(msg_obj: object) -> (
        Optional[object], Optional[str], Optional[str]
    ):
        for part in msg_obj.walk():
            # ContentTypeがmultipartの場合は
            # 実際のコンテンツ(subpart)ではないためスキップ
            if part.get_content_maintype() == 'multipart':
                continue
            filename = part.get_filename()
            # ファイル名がある場合は添付ファイル
            if filename:
                return(
                    # 添付ファイルデータ
                    part.get_payload(decode=True),
                    # 添付ファイル名
                    filename,
                    # 添付ファイルのMimetype
                    mimetypes.guess_type(filename)[0]
                )
        return None, None, None

    # メッセージオブジェクトから本文を取得する
    def parse_mail_content(msg_obj: object) -> str:
        for part in msg_obj.walk():
            # ContentTypeがmultipartの場合は
            # 実際のコンテンツ(subpart)ではないためスキップ
            if part.get_content_maintype() == 'multipart':
                continue
            filename = part.get_filename()
            # ファイル名がない場合は本文
            if not filename:
                charset = str(part.get_content_charset())
                if charset:
                    return part.get_payload(decode=True).decode(charset)
                else:
                    return part.get_payload(decode=True)

    # 音声ファイルをS3に保存する
    def put_audio_data_to_bucket(
        bucket: str,
        data: object,
        filename: str,
        mimetype: str,
        s3: BaseClient
    ) -> None:
        s3.Bucket(bucket).put_object(
            ACL='private',
            Body=data,
            Key=filename,
            ContentType=mimetype
        )

    # 音声ファイルのS3のuriを作成する
    def create_media_file_uri(bucket: str, filename: str) -> str:
        return 'https://s3.amazonaws.com/' + bucket + '/' + filename

    # LambdaのリクエストIDからユニークなジョブ名を作成する
    def create_unique_job_name(context):
        return 'job_' + context.aws_request_id

    # 音声ファイルに対するTranscribeジョブの実行を開始する
    def start_transcribe_job(
        job_name: str,
        media_file_uri: str,
        media_format: str,
        language_code: str,
        client: BaseClient
    ) -> None:
        client.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={
                'MediaFileUri': media_file_uri
            },
            MediaFormat=media_format,
            LanguageCode=language_code
        )

    # Transcribeのjob完了まで待機し、完了したら結果のステータスを取得する
    def wait_for_job_completion(job_name: str, client: BaseClient) -> str:
        job_status = 'INIT'
        while True:
            time.sleep(10)
            job_status = client.get_transcription_job(
                TranscriptionJobName=job_name
            )['TranscriptionJob']['TranscriptionJobStatus']
            print(job_status)
            if job_status == 'COMPLETED' or job_status == 'FAILED':
                break
        return job_status

    # Transcribeの実行結果スクリプトが配置されたuriを取得する
    def get_transcript_file_uri(
        job_name: str,
        transcribe_client: BaseClient
    ) -> str:
        return transcribe_client.get_transcription_job(
            TranscriptionJobName=job_name
        )['TranscriptionJob']['Transcript']['TranscriptFileUri']

    # uriから実行結果スクリプトをjsonで取得する
    def get_transcript_json(transcript_file_uri: str) -> dict:
        return json.loads(
            urllib.request.urlopen(
                urllib.request.Request(
                    transcript_file_uri
                )
            ).read()
        )

    # 実行結果スクリプトから文字起こしテキストをパースする
    def parse_transcript_text(transcript_json: dict) -> str:
        return transcript_json['results']['transcripts'][0]['transcript']

    # 環境変数からTrello APIの情報をパースする
    def parse_environment_values() -> (str, str, str):
        env = os.environ
        return (
            env['TRELLO_API_ENDPOINT'],
            env['TRELLO_KEY'],
            env['TRELLO_TOKEN'],
            env['TRELLO_IDLIST_READY']
        )

    # Trelloカードのタイトル（例：[Rusuden]2020/02/01 12:05 着信）を作成
    def create_card_title(fn: str) -> str:
        return '[Rusuden]' + \
                fn[0:4] + '/' + \
                fn[4:6] + '/' + \
                fn[6:8] + ' ' + \
                fn[8:10] + ':' + \
                fn[10:12] + ' ' + \
                '着信'

    # Trelloカードの説明の文章を作成
    def create_card_discript(
        transcript_text: str,
        mail_content: str
    ) -> str:
        return transcript_text + '\n\n' + mail_content

    # Trelloにカードを作成する
    def create_trello_card(
        trello_key: str,
        trello_token: str,
        trello_idlist_ready: str,
        card_title: str,
        card_discript: str,
        trello_endpoint: str
    ) -> None:
        params = {
            'key': trello_key,
            'token': trello_token,
            'idList': trello_idlist_ready,
            'pos': 'top',
            'name': card_title,
            'desc': card_discript
        }
        url = '{}?{}'.format(
            trello_endpoint,
            urllib.parse.urlencode(params)
        )
        dummy_data = json.dumps('dummy').encode()
        urllib.request.urlopen(
            urllib.request.Request(
                url,
                dummy_data
            )
        )

    try:
        if event['Records'][0]['eventName'] == 'ObjectCreated:Put':
            main()
    except:
        print("only invoked by \'ObjectCreated:Put\'.")
