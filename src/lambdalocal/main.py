import subprocess
import sys
import typing

import boto3
import click
import yaml

DEFAULT_ENCODING = "UTF-8"
DEFAULT_LAMBDA_ROLE = "arn:aws:iam::000000000000:role/lambda-role"


class LambdaTemplateResource(typing.TypedDict):
    Name: str
    Handler: str
    Runtime: str
    Environment: typing.Dict[str, str]


class LambdaTemplateConfig(dict):
    @classmethod
    def load(cls, template_file: str):
        config: LambdaTemplateResource = {}
        with open(template_file, "r", encoding="utf-8") as lambda_config:
            try:
                data = yaml.safe_load(lambda_config)
                resources = data.get("Resources")
                for key in resources.keys():
                    config = resources.get(key)
                    break
            except yaml.YAMLError as err:
                print("Failed to parse function configuration", err)
                sys.exit(1)

        return cls(**config)


class LambdaClient:
    def __init__(self, region: str):
        self.client = boto3.client(
            "lambda",
            endpoint_url="http://localhost:4566",
            region_name=region,
            aws_access_key_id="asdf",
            aws_secret_access_key="1234",
        )

    def function_exists(self, name: str) -> bool:
        try:
            self.client.get_function(FunctionName=name)
            return True
        except self.client.exceptions.ResourceNotFoundException:
            return False


class ApiGatewayClient:
    def __init__(self, region: str):
        self.client = boto3.client(
            "apigateway",
            endpoint_url="http://localhost:4566",
            region_name=region,
            aws_access_key_id="asdf",
            aws_secret_access_key="1234",
        )

    def get_rest_api(self, name: str):
        response = self.client.get_rest_apis()
        matching_apis = filter(
            lambda item: item.get("name") == name, response.get("items")
        )
        for api in matching_apis:
            return api

        return None

    def get_resource_by_path(self, rest_api_id, path):
        resources = self.client.get_resources(restApiId=rest_api_id)
        for item in filter(
            lambda item: item.get("path") == path,
            resources.get("items"),
        ):
            return item
        return None

    def rest_api_resource(self, rest_api_id, parent_id, path, path_part):
        resource = self.get_resource_by_path(rest_api_id, path)
        if resource:
            return resource

        return self.client.create_resource(
            restApiId=rest_api_id,
            parentId=parent_id,
            pathPart=path_part,
        )


@click.group(help="Run aws lambda locally with localstack")
def cli():
    """Main entrypoint for chaostoolkit-cliutil"""


@cli.command(
    "deploy",
    help=("Deploy lambda function zip to localstack"),
)
@click.option("--region", required=True, help="The AWS region")
@click.option(
    "--template",
    "-t",
    required=True,
    type=click.Path(exists=True),
    help="The path of the lambda template",
)
@click.argument("file", required=True, type=click.Path(exists=True))
def deploy(region: str, template: str, file: str):

    config = LambdaTemplateConfig.load(template)

    with open(file, "rb") as file_data:
        bytes_content = file_data.read()

    lambda_client = LambdaClient(region)

    if not lambda_client.function_exists(config["Name"]):
        lambda_client.client.create_function(
            FunctionName=config["Name"],
            Runtime=config["Runtime"],
            Code={"ZipFile": bytes_content},
            PackageType="Zip",
            Handler=config["Handler"],
            Environment={"Variables": config["Environment"]},
            Role=DEFAULT_LAMBDA_ROLE,
        )
    else:
        lambda_client.client.update_function_code(
            FunctionName=config["Name"],
            ZipFile=bytes_content,
        )


@cli.command(
    "apigw",
    help=("Deploy lambda apigw"),
)
@click.option("--region", required=True, help="The AWS region")
@click.option(
    "--template",
    "-t",
    required=True,
    type=click.Path(exists=True),
    help="The path of the lambda template",
)
def apigw(template: str, region: str):
    config = LambdaTemplateConfig.load(template)

    lambda_client = LambdaClient(region)
    apigw_client = ApiGatewayClient(region)

    response = lambda_client.client.get_function(FunctionName=config["Name"])
    lambda_arn = response.get("Configuration").get("FunctionArn")

    rest_api = apigw_client.get_rest_api(config["Name"])
    if not rest_api:
        rest_api = apigw_client.client.create_rest_api(name=config["Name"])

    rest_api_id = rest_api.get("id")

    parent_resource = apigw_client.get_resource_by_path(rest_api_id, "/")
    resource = apigw_client.rest_api_resource(
        rest_api_id,
        parent_resource.get("id"),
        "/{proxy+}",
        "{proxy+}",
    )
    apigw_client.client.put_method(
        restApiId=rest_api_id,
        resourceId=resource.get("id"),
        httpMethod="ANY",
        requestParameters={},
        authorizationType="NONE",
    )
    apigw_client.client.put_integration(
        restApiId=rest_api_id,
        resourceId=resource.get("id"),
        httpMethod="ANY",
        type="AWS_PROXY",
        integrationHttpMethod="POST",
        uri=f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations",
        passthroughBehavior="WHEN_NO_MATCH",
    )

    apigw_client.client.create_deployment(
        restApiId=rest_api_id,
        stageName="local",
    )

    print(
        f"Url: http://localhost:4566/restapis/{rest_api_id}/local/_user_request_/health"
    )


@cli.command(
    "forward",
    help=("Creates an http endpoint to forward requests to rest-api"),
)
@click.option("--region", required=True, help="The AWS region")
@click.option(
    "--template",
    "-t",
    required=True,
    type=click.Path(exists=True),
    help="The path of the lambda template",
)
@click.option(
    "--net",
    required=False,
    help="The docker network to use for the apiproxy container",
)
def forward_rest_api(template: str, region: str, net: str):
    config = LambdaTemplateConfig.load(template)

    apigw_client = ApiGatewayClient(region)

    rest_api = apigw_client.get_rest_api(config["Name"])
    if not rest_api:
        print("Could not find valid rest api")
        sys.exit(1)

    rest_api_id = rest_api.get("id")
    apigw_path = f"/restapis/{rest_api_id}/local/_user_request_/"

    cmd = [
        "docker",
        "run",
        "--rm",
        "-d",
        "--net",
        net or "default",
        "--name",
        "apiproxy",
        "-p",
        "8889:8889",
        "-p",
        "8999:8999",
        "mitmproxy/mitmproxy",
        "mitmweb",
        "--web-host",
        "0.0.0.0",
        "--web-port",
        "8999",
        "--map-remote",
        f"|/rest/|{apigw_path}",
        "--mode",
        "reverse:http://localstack:4566/",
        "--listen-port",
        "8889",
        "--ssl-insecure",
    ]
    subprocess.run(cmd)


if __name__ == "__main__":
    cli()
