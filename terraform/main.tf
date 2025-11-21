provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_pubsub_topic" "metrics" {
  name = "metrics-topic"
}

resource "google_pubsub_topic" "events" {
  name = "events-topic"
}

resource "google_container_cluster" "primary" {
  name     = "pulse-gke"
  location = var.zone
  initial_node_count = 1
  deletion_protection = false
  
  node_config {
    machine_type = "e2-micro"
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
}
