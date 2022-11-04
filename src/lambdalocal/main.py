import click

import boto3

DEFAULT_ENCODING = "UTF-8"
DEFAULT_LAMBDA_ROLE = "arn:aws:iam::000000000000:role/lambda-role"


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
@click.option("--name", required=True, help="The name of the lambda function")
@click.option(
    "--handler", required=True, help="The name of the lambda function handler"
)
@click.option(
    "--runtime", required=False, default="python3.9", help="The lambda runtime"
)
@click.argument("file", required=True, type=click.Path(exists=True))
def deploy(name: str, region: str, handler: str, runtime: str, file: str):

    with open(file, "rb") as file_data:
        bytes_content = file_data.read()

    lambda_client = LambdaClient(region)

    if not lambda_client.function_exists(name):
        lambda_client.client.create_function(
            FunctionName=name,
            Runtime=runtime,
            Code={"ZipFile": bytes_content},
            PackageType="Zip",
            Handler=handler,
            Role=DEFAULT_LAMBDA_ROLE,
        )
    else:
        result = lambda_client.client.update_function_code(
            FunctionName=name,
            ZipFile=bytes_content,
        )
        print(result)


@cli.command(
    "apigw",
    help=("Deploy lambda apigw"),
)
@click.option("--region", required=True, help="The AWS region")
@click.option("--function-name", required=True, help="The name of the lambda function")
def apigw(function_name: str, region: str):
    lambda_client = LambdaClient(region)
    apigw_client = ApiGatewayClient(region)

    response = lambda_client.client.get_function(FunctionName=function_name)
    lambda_arn = response.get("Configuration").get("FunctionArn")

    rest_api = apigw_client.get_rest_api(function_name)
    if not rest_api:
        rest_api = apigw_client.client.create_rest_api(name=function_name)

    rest_api_id = rest_api.get("id")

    parent_resource = apigw_client.get_resource_by_path(rest_api_id, "/")
    resource = apigw_client.rest_api_resource(
        rest_api_id,
        parent_resource.get("id"),
        "/{somethingId}",
        "{somethingId}",
    )
    apigw_client.client.put_method(
        restApiId=rest_api_id,
        resourceId=resource.get("id"),
        httpMethod="GET",
        requestParameters={"method.request.path.somethingId": True},
        authorizationType="NONE",
    )
    apigw_client.client.put_integration(
        restApiId=rest_api_id,
        resourceId=resource.get("id"),
        httpMethod="GET",
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


if __name__ == "__main__":
    cli()
