#!/usr/bin/env ruby
# frozen_string_literal: true

require "yaml"
require "shellwords"

ROOT = File.expand_path("..", __dir__)
SKILL_FILES = Dir[File.join(ROOT, "skills/*/SKILL.md")].sort

SECRET_PATTERNS = [
  Regexp.new(["BEGIN", ".*", "PRIVATE", "KEY"].join(" ")),
  Regexp.new(["github", "pat", ""].join("_")),
  Regexp.new(["ghp", "[A-Za-z0-9]"].join("_")),
  /sk-[A-Za-z0-9]/,
  /AKIA[0-9A-Z]{16}/,
  /xox[baprs]-/,
  Regexp.new(["client", "secret"].join("_"), Regexp::IGNORECASE),
  Regexp.new(["access", "token"].join("_"), Regexp::IGNORECASE),
  Regexp.new(["refresh", "token"].join("_"), Regexp::IGNORECASE),
  /password\s*=/i,
  /api[_-]?key\s*=/i,
  /secret\s*=/i
].freeze

INTERNAL_COPY_PHRASES = [
  ["private", "workflow"],
  ["private", "project", "workflows"],
  ["before", "public"],
  ["public", "release"],
  ["prove", "nance"],
  ["marketplace", "packaging", "can", "come", "later"],
  ["start", "with", "github", "distribution"]
].map { |parts| parts.join(" ") }.freeze

def fail_with(message)
  warn "ERROR: #{message}"
  exit 1
end

fail_with("no skills found") if SKILL_FILES.empty?

names = {}

SKILL_FILES.each do |path|
  content = File.read(path)
  frontmatter = content.split(/^---\s*$/, 3)[1]
  fail_with("#{path}: missing YAML frontmatter") unless frontmatter

  data = YAML.safe_load(frontmatter)
  name = data["name"]
  description = data["description"]

  fail_with("#{path}: missing name") if name.to_s.empty?
  fail_with("#{path}: invalid name #{name.inspect}") unless name.match?(/\A[a-z0-9-]{1,64}\z/)
  fail_with("#{path}: duplicate skill name #{name}") if names.key?(name)
  fail_with("#{path}: missing description") if description.to_s.empty?
  fail_with("#{path}: description exceeds 200 characters") if description.length > 200

  names[name] = path
end

tracked_files = `git -C #{ROOT.shellescape} ls-files`.lines.map(&:strip)

tracked_files.each do |relative|
  path = File.join(ROOT, relative)
  fail_with("#{relative}: tracked symlink") if File.symlink?(path)
  next unless File.file?(path)

  content = File.read(path, mode: "rb")
  text = content.force_encoding("UTF-8")
  next unless text.valid_encoding?

  SECRET_PATTERNS.each do |pattern|
    fail_with("#{relative}: matched sensitive pattern #{pattern.inspect}") if text.match?(pattern)
  end

  INTERNAL_COPY_PHRASES.each do |phrase|
    fail_with("#{relative}: matched internal-copy phrase #{phrase.inspect}") if text.downcase.include?(phrase)
  end
end

puts "Validated #{SKILL_FILES.length} skills."
