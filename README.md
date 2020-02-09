# rusuden

## はじめに

わたしが現在利用しているIP電話サービス（楽天モバイル[SMARTalk](https://ip-phone-smart.jp/smart/smartalk/)）には、不在着信時の留守電を音声ファイル(wav形式)にしてメール送付してくれる機能があります。留守電の内容を確認したいときにはメールに添付された音声ファイルを再生して聞けばいいのでこれはこれで便利です。

しかし、もっと便利さを突き詰めたい！ということで、メール送付された音声ファイルに対してさらに**Amazon Transcribe**による文字起こしを行い、わざわざ音声を再生しなくても留守電の内容を把握できるシステムを作ってみたのでAmazon Transcribeの活用例としてご紹介します。

## Amazon Transcribeとは

- https://aws.amazon.com/jp/transcribe

> Amazon Transcribe は、自動音声認識 (ASR、automatic speech recognition) と呼ばれる深層学習プロセスを使って迅速かつ高精度に音声をテキストに変換します。Amazon Transcribe は、カスタマーサービスの通話の文字起こし、クローズドキャプションや字幕の自動作成、完全に検索可能なアーカイブを作成する際におけるメディア資産のメタデータの生成に使用できます。 Amazon Transcribe Medical を使用すると、医療関連の音声をテキストに変換する機能を臨床ドキュメントアプリケーションに追加できます。

- https://docs.aws.amazon.com/ja_jp/transcribe/latest/dg/what-is-transcribe.html

現在、日本語 (ja-JP)を含む31の言語の音声認識に対応しています。

## システム構成

システム構成は以下の通り。

- SMARTalk
- Microsoft Office 365
  - Outlook
- AWS
  - Amazon SES（eu-west-1）
  - Amazon S3
  - AWS Lambda（ap-northeast-1）
  - Amazon Transcribe（ap-northeast-1）
- Trello

なお、Amazon SESは`ap-northeast-1`（東京）リージョンでは現在時点でまだ利用できないため、代わりに`eu-west-1`（アイルランド）リージョンを利用しました。

## システム動作概要

システムの動作の概要は以下の通り。

1. 発信者はSMARTalkの電話番号に架電し、不在着信となる
1. 発信者は留守電音声を登録する
1. SMARTalkは留守電音声を添付した留守電通知メールをOffice 365に送信する
1. Office 365は留守電通知メールをSESに自動転送する
1. SESは受信したメールをS3バケット（Prefix：`mailraw/`）にPUTする
1. S3 PUTトリガーで起動したLambdaはS3バケットに格納されたメールデータを取得する
1. Lambdaはメールデータから留守電音声ファイルをパースしてS3バケットにPUTする
1. Lambdaは留守電音声ファイルに対するAmazon Transcribeジョブをキックする
1. Amazon TranscribeはS3に格納された音声ファイルを取得し、音声認識ジョブを実行する
1. Amazon Transcribeはジョブ実行が完了したら音声認識結果のファイル（JSON）をS3バケットに保存する
1. Lambdaは音声認識結果のファイルをS3バケットから取得する
1. Lambdaは音声認識結果を記載したTrelloカードを作成する
1. ユーザーはTrelloのカードで留守電の内容を把握する

![image.png](https://image.docbase.io/uploads/5758a417-dcb7-4bf4-92a9-62f6d293a9c4.png)

## 構築手順

### Amazon SESのドメイン設定

下記の参考URLを参考に、`eu-west-1`リージョンの[Amazon SES](https://console.aws.amazon.com/ses/home)で転送メールの受信アドレスに利用したいインターネットドメインを登録・検証する。

![image.png](https://image.docbase.io/uploads/89b9052e-aa84-45c1-b047-f8c066620e76.png)

#### 参考

- [Amazon SES でのドメインの検証（AWSドキュメント）](https://docs.aws.amazon.com/ja_jp/ses/latest/DeveloperGuide/verify-domains.html)

### TrelloのAPI情報の取得

下記の参考URLを参考に、TrelloにAPIでカードを作成するために必要となる以下３つの情報を取得する。

- API Key
- API Token
  - 有効期限は無期限
  - Read/Write権限
- リストID
  - 文字起こし結果テキストを記載したカードを作成するリストのID

#### 参考

- [Trello API を叩いてカードを作成する方法（curl利用）（Qiita）](https://qiita.com/isseium/items/8eebac5b79ff6ed1a180)
- [TRELLO REST API](https://developers.trello.com/reference)

### SSM Parameter設定

`ap-northeast-1`および`eu-west-1`リージョンのAWS Systems Managerの[パラメーターストア](https://console.aws.amazon.com/systems-manager/parameters)（SSM Parameter）で以下のパラメータを設定する。

|項番  |リージョン  |名前  |値  |
|---|---|---|---|
|1  |ap-northeast-1  |rusuden-targetBucketName  |メールデータおよび音声データを格納するバケット名（バケット自体は[AWSリソースのデプロイ]で作成する）  |
|2  |ap-northeast-1  |trelloKey  | [TrelloのAPI情報の取得]で取得したTrelloのAPI Key |
|3  |ap-northeast-1  |trelloToken  |[TrelloのAPI情報の取得]で取得したTrelloのAPI Token  |
|4  |ap-northeast-1  |trelloIdListReady  |[TrelloのAPI情報の取得]で取得したTrelloのリストID  |
|5  |ap-northeast-1  |trelloApiEndpoint  |`https://trello.com/1/cards`  |
|6  |eu-west-1  |rusuden-targetBucketName  |項番1と同じ  |
|7  |eu-west-1  |rusuden-recipientAddress  |[Amazon SESのドメイン設定]で設定したドメインを持つメールアドレス  |
|8  |eu-west-1  |defaultSesRuleSet  |SESに自動転送されたメールをS3バケットにPUTするRecipient Ruleを追加するRule Set  |

### AWSリソースのデプロイ

- 変数定義

```shell
### 作成されるAWSリソースにプレフィクスとなる文字列
$ APP_NAME=rusuden
### デプロイ時の作業パス
$ workdir=${PWD}/$APP_NAME
### Lambdaコードをデプロイするための一時的なバケット。グローバルにユニークなら任意の名前でOK
$ backet_name=${app_name}-$(echo -n $(aws sts get-caller-identity | jq -r .Account) | md5sum | cut -c 1-10)
### デプロイ用バケットを未作成なら作成
$ aws s3 mb s3://$backet_name
```

- Githubからソースコードをクローン

```shell
$ git clone https://github.com/r-wakatsuki/rusuden.git $workdir
```

- Lambda関数コード用のzipして、S3バケットにアップロード

```shell
$ zip -j ${workdir}/function.zip ${workdir}/${app_name}-aws-function-00/*
$ aws s3 mv ${workdir}/function.zip s3://${backet_name}/function.zip
```

- ap-northeast-1リージョンで必要なリソースをCloudFormationでデプロイ

```shell
$ aws cloudformation create-stack \
  --stack-name ${APP_NAME}-aws-stack-00 \
  --template-body file://${workdir}/${APP_NAME}-aws-stack-00.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=appName,ParameterValue=$APP_NAME \
    ParameterKey=bucketName,ParameterValue=$bucket_name \
  --region ap-northeast-1
```

- （必要な場合）Amazon SESのActive Rule Setの設定

[SSM Parameter設定]の項番8で設定した値を指定（既存または新規作成するRule Set名）

```shell
$ RULE_SET=default-rule-set
```

Rule Setを新規作成する場合

```shell
$ aws ses create-receipt-rule-set \
  --rule-set-name $RULE_SET \
  --region eu-west-1
```

利用するRule Setが未作成の場合

```shell
$ aws ses set-active-receipt-rule-set \
  --rule-set-name $RULE_SET \
  --region eu-west-1
```

- eu-west-1リージョンで必要なリソースをCloudFormationでデプロイCloudFormationでデプロイ

```shell
$ aws cloudformation create-stack \
  --stack-name ${APP_NAME}-aws-stack-01 \
  --template-body file://${workdir}/${APP_NAME}-aws-stack-01.yml \
  --parameters ParameterKey=appName,ParameterValue=$APP_NAME \
  --region eu-west-1
```

### Office 365からSESへの自動転送ルール設定

Outlook（Office 365）の[ルール](https://outlook.office.com/mail/options/mail/rules)にて以下のルールを作成。

|ルール  |  |  |
|---|---|---|
|条件  |差出人  |`*****@fusioncom.co.jp`  |
|  |宛先  |SMARTalk留守電通知メールの送信先に指定したアドレス  |
|  |件名に含まれている  |`【SMARTalk】メッセージお預かり通知`  |
|  |添付ファイルがある  |  |
|アクション  |指定のアドレスに転送  |[SSM Parameter設定]の項番7で指定したメールアドレス |

![image.png](https://image.docbase.io/uploads/98d2b94e-3a7e-494b-9eff-09bd6df0f833.png)

## 動作確認

- SMARTalkの電話番号に架電し、留守電を登録する。

- SMARTalkのポータル画面を見ると、留守電が音声データとして登録されています。
![image.png](https://image.docbase.io/uploads/db981284-4287-437b-99e3-1162730a9188.png)

- Office 365のメッセージ追跡ログを見ると、留守電通知メールがSESのメールアドレスに自動転送されています。
![image.png](https://image.docbase.io/uploads/0ed3666b-6a95-47fd-9570-1db2c6d3dbcf.png)

- S3バケットを見ると、`mailraw/`プレフィクスでメールデータが格納されています。
![image.png](https://image.docbase.io/uploads/ea1d2b3f-2459-4112-9460-b4077d09e290.png)

- さらにS3バケットのトップを見ると、音声ファイルが格納されています。
![image.png](https://image.docbase.io/uploads/a7d8e65c-834e-44d7-8ef7-a074e32c9e38.png)

- Amazon Transcribeのジョブ一覧を見ると、音声認識ジョブが作成されています。
![image.png](https://image.docbase.io/uploads/3980b052-3bd3-4d4c-81ff-b5a018a2abc9.png)

- CloudWatch LogsでLambdaの実行ログを見ると、`IN_PROGRESS`の間でTranscribeジョブが実行され、19秒の音声ファイルでジョブ実行に100秒近くを要したことが分かります。
![image.png](https://image.docbase.io/uploads/0c1122b7-5cd4-41f9-bff9-4488e33204b3.png)

- Trelloのリストを見ると、指定した件名でカードが作成されています。
![image.png](https://image.docbase.io/uploads/38157823-469a-46c6-8691-56ef4ddb61de.png)

- カードを開くと、メール本文の上部赤枠内に文字起こし結果が確認できます。
![image.png](https://image.docbase.io/uploads/ebfb9844-a6a2-4e59-9dd2-7890db833510.png)

- 文字起こし結果と実際に話した内容の比較

漢字変換や文節の分け方は概ね合っており、留守電の内容が把握できる精度の文字起こし結果を得ることができました。

```
### 文字起こし結果
お 世話 に なっ て おり ます 私 クラス メソッド サポート の 若月 と 申し ます お 問い合わせ 頂い て おり まし た ラムダ の 実行 間隔 の 件 に つい て お 電話 さ せ て いただき まし た 改めて お 電話 差し上げ ます 失礼 いたし ます
```

```
 ### 実際に話した内容
お世話になっております。わたしクラスメソッドサポートの若槻と申します。お問い合わせ頂いておりましたLambdaの実行間隔の件についてお電話させて頂きました。改めてお電話差し上げます。失礼いたします。
```

## おわりに

Amazon Transcribeの活用例のご紹介でした。

以前まではAmazon Transcribeは日本語に対応していなかったので、わたしは同様の機能を実現するために[Microsoft Power AutometeとGoogle Cloud Speechを利用したシステム](https://qiita.com/r-wakatsuki/items/f1d820fbe6873a56074e)を作成して利用していましたが、[昨年10月についに日本語対応がリリースされた](https://aws.amazon.com/jp/about-aws/whats-new/2019/11/amazon-transcribe-now-supports-speech-to-text-in-7-additional-languages/)ので、今回AWSに移植をしてみたという経緯がありました。

参考になれば幸いです。

以上
