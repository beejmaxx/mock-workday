resource "aws_ecr_repository" "main" {
  name         = "mock-workday"
  force_delete = true
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "main" {
  repository = aws_ecr_repository.main.name
  policy = jsonencode({ rules = [{
    rulePriority = 1
    description  = "Keep the last five images"
    selection = {
      tagStatus   = "any"
      countType   = "imageCountMoreThan"
      countNumber = 5
    }
    action = { type = "expire" }
  }] })
}

output "repository_url" {
  value = aws_ecr_repository.main.repository_url
}
