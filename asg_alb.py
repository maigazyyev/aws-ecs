import pulumi
import pulumi_aws as aws
import base64

config = pulumi.Config()
vpc_stack = pulumi.StackReference('dev-project-core-vpc')                  
dev_vpc_id = vpc_stack.get_output('vpc-id')
private_subnet_id1 = vpc_stack.get_output('private-subnet1')
private_subnet_id2 = vpc_stack.get_output('private-subnet2')
public_subnet_id1 = vpc_stack.get_output('public-subnet1')
public_subnet_id2 = vpc_stack.get_output('public-subnet2')

ecs_ami = "ami-0b9a500fd6b7c052c" # Amazon Linux AMI 2.0.20230109 x86_64 ECS HVM GP2 ECS-Iptimized
ssh_key = "your-ssh-key"
env = "dev"
project = "project-name"
resource_name = env + "-" + project

########Auto Scaling Group ##########

dev_asg_sg = aws.ec2.SecurityGroup(
    resource_name + "-asg",
    name=resource_name + "-asg",
    description=f"sg for {'asg'}",
    vpc_id=dev_vpc_id,
    ingress=[
        {
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "cidr_blocks": [
                "10.129.0.0/16",  #dev-vpc     you can add vpc of another env, but they need to be attached via TGW,
            ],                    #            if you are using multiple AWS accounts
        },
        {
            "protocol": "icmp",
            "from_port": -1,
            "to_port": -1,
            "cidr_blocks": [
                "10.129.0.0/16",
            ],
        },
    ],
    egress=[
        {
            "protocol": "-1", 
            "from_port": 0, 
            "to_port": 0, 
            "cidr_blocks": ["0.0.0.0/0"]
        }
    ],
)

################### Mounting EFS Volume so, we can use between services ################### 
################### If you for some reasons can't use S3                ###################
user_data = """               
#!/bin/bash                   
echo ECS_CLUSTER=dev-project-core-cluster >> /etc/ecs/ecs.config;
echo "fs-0648d1231daf824f15.efs.eu-north-1.amazonaws.com:/ /some/path nfs4 nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2,noresvport,_netdev 0 0" >> /etc/fstab;
mkdir /some/path;
mount -a;
"""

encoded_user_data = base64.b64encode(user_data.encode("utf-8")).decode("utf-8")

dev_launchtemplate = aws.ec2.LaunchTemplate("dev-project-ecs-launch-template",
    image_id=ecs_ami,
    key_name=ssh_key,
    vpc_security_group_ids=[dev_asg_sg.id],
    block_device_mappings=[{                        ##Additional volume
        "device_name": "/dev/sda1",
        "ebs": {
            "volume_size": 30,
            "volume_type": "gp2",
        },
    }],
    iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
        name="ecsInstanceRole",
    ),
    user_data=encoded_user_data,
    tags={
        "Name": "dev-ecs-launch-template",
    },
    instance_type="t3.large")

dev_asg_for_ecs = aws.autoscaling.Group("dev-project-asg",
    vpc_zone_identifiers=[private_subnet_id1, private_subnet_id2],
    desired_capacity=2,
    max_size=2,
    min_size=1,
    target_group_arns = ["your-target-group-arns"],
    launch_template={
        "id": dev_launchtemplate.id,
        "version": "$Latest",
    })

########Load Balancer Security Group ##########

dev_alb_sg = aws.ec2.SecurityGroup(
    resource_name + "-alb",
    name=resource_name + "-alb",
    description=f"sg for {'alb'}",
    vpc_id=dev_vpc_id,
    ingress=[
        {
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "cidr_blocks": [
                "10.129.0.0/16",  #dev-vpc
            ],
        },
        {
            "protocol": "icmp",
            "from_port": -1,
            "to_port": -1,
            "cidr_blocks": [
                "10.129.0.0/16",
            ],
        },
    ],
    egress=[
        {
            "protocol": "-1", 
            "from_port": 0, 
            "to_port": 0, 
            "cidr_blocks": ["0.0.0.0/0"]
        }
    ],
)

########Load Balancer ##########

dev_load_balancer = aws.lb.LoadBalancer("dev-project-ecs-alb",
    security_groups=[dev_alb_sg.id],
    subnets=[public_subnet_id1, public_subnet_id2],
    load_balancer_type = "application",
    ip_address_type = "ipv4",
    enable_deletion_protection = True,
    tags={
        "Name": "dev-project-alb",
    },
)

http_listener = aws.lb.Listener("dev-alb-http-listener",
    load_balancer_arn=dev_load_balancer.arn,
    port=80,
    protocol="HTTP",
    default_actions=[aws.lb.ListenerDefaultActionArgs(
        type="redirect",
        redirect=aws.lb.ListenerDefaultActionRedirectArgs(
            port="443",
            protocol="HTTPS",
            status_code="HTTP_301",
        ),
    )])

######## I added HTTPS CERTIFICATE manually ########### So here, i just put ARN
######## Target group I added manually via AWS console too, because of I tested this code ##### But you can add here target group.
https_listener = aws.lb.Listener("dev-alb-https-listener",
    load_balancer_arn=dev_load_balancer.arn,
    port=443,
    protocol="HTTPS",
    ssl_policy="ELBSecurityPolicy-2016-08",
    certificate_arn="arn:aws:acm:eu-north-1:123456789:certificate/6f8c8fadd7-9b2b12-4c8-aaa3-25b3004737127",
    default_actions=[aws.lb.ListenerDefaultActionArgs(
        type="forward",
        target_group_arn="arn:aws:elasticloadbalancing:eu-north-1:123456789:targetgroup/dev-project-targetgroup/jkljlkf9012"
    )]
)

backend_service_path1 = aws.lb.ListenerRule("backend-clients-path-api-rule",
    listener_arn=https_listener.arn,
    priority=98,
    actions=[aws.lb.ListenerRuleActionArgs(
        type="forward",
        target_group_arn="arn:aws:elasticloadbalancing:eu-north-1:123456789:targetgroup/dev-project-targetgroup/klk38128" #9001/api
    )],
    conditions=[
        aws.lb.ListenerRuleConditionArgs(
            path_pattern=aws.lb.ListenerRuleConditionPathPatternArgs(
                values=["/api/"],
            ),
        ),
        aws.lb.ListenerRuleConditionArgs(
            host_header=aws.lb.ListenerRuleConditionHostHeaderArgs(
                values=["dev.project.com", "www.dev.project.com"],
            ),
        ),
    ]
)

backend_service_path2 = aws.lb.ListenerRule("backend-clients-path-admin-rule",
    listener_arn=https_listener.arn,
    priority=99,
    actions=[aws.lb.ListenerRuleActionArgs(
        type="forward",
        target_group_arn="arn:aws:elasticloadbalancing:eu-north-1:123456789:targetgroup/dev-project-targetgroup/salkdl1283", #9001/admin
    )],
    conditions=[
        aws.lb.ListenerRuleConditionArgs(
            path_pattern=aws.lb.ListenerRuleConditionPathPatternArgs(
                values=["/admin/"],
            ),
        ),
        aws.lb.ListenerRuleConditionArgs(
            host_header=aws.lb.ListenerRuleConditionHostHeaderArgs(
                values=["dev.project.com", "www.dev.project.com"],
            ),
        ),
    ]
)

frontend_service = aws.lb.ListenerRule("frontend-clients-rule",
    listener_arn=https_listener.arn,
    priority=102,
    actions=[aws.lb.ListenerRuleActionArgs(
        type="forward",
        target_group_arn="arn:aws:elasticloadbalancing:eu-north-1:123456789:targetgroup/dev-project-targetgroup/dals3j12388", #3000/
    )],
    conditions=[
        aws.lb.ListenerRuleConditionArgs(
            path_pattern=aws.lb.ListenerRuleConditionPathPatternArgs(
                values=["/"],
            ),
        ),
        aws.lb.ListenerRuleConditionArgs(
            host_header=aws.lb.ListenerRuleConditionHostHeaderArgs(
                values=["dev.project.com", "www.dev.project.com"],
            ),
        ),
    ]
)





