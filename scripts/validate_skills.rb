#!/usr/bin/env ruby
# frozen_string_literal: true

require "yaml"
require "shellwords"
require "set"
require "pathname"

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

def relative_to_root(path)
  Pathname.new(path).relative_path_from(Pathname.new(ROOT)).to_s
end

fail_with("no skills found") if SKILL_FILES.empty?

names = {}
tracked_files = `git -C #{ROOT.shellescape} ls-files`.lines.map(&:strip)
tracked_file_set = tracked_files.to_set

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

  directory_name = File.basename(File.dirname(path))
  fail_with("#{path}: frontmatter name #{name.inspect} does not match directory #{directory_name.inspect}") unless name == directory_name

  content.scan(/`(references\/[^`]+)`/).flatten.each do |reference|
    reference_path = File.join(File.dirname(path), reference)
    reference_relative = relative_to_root(reference_path)
    fail_with("#{path}: referenced file #{reference} is missing") unless File.file?(reference_path)
    fail_with("#{path}: referenced file #{reference} is not tracked") unless tracked_file_set.include?(reference_relative)
  end

  openai_metadata = File.join(File.dirname(path), "agents/openai.yaml")
  fail_with("#{path}: missing agents/openai.yaml metadata") unless File.file?(openai_metadata)

  names[name] = path
end

Dir[File.join(ROOT, "skills/*/agents/*.yaml")].sort.each do |path|
  data = YAML.safe_load(File.read(path))
  interface = data.is_a?(Hash) ? data["interface"] : nil
  fail_with("#{path}: missing interface metadata") unless interface.is_a?(Hash)

  %w[display_name short_description default_prompt].each do |key|
    fail_with("#{path}: missing interface.#{key}") if interface[key].to_s.empty?
  end
end

["README.md", "docs/examples.md"].each do |relative|
  content = File.read(File.join(ROOT, relative))
  names.each_key do |name|
    fail_with("#{relative}: missing #{name}") unless content.include?(name)
  end
end

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

  next unless relative.end_with?(".md")

  text.scan(/\[[^\]]+\]\(([^)\s]+)(?:\s+"[^"]*")?\)/).flatten.each do |target|
    next if target.start_with?("#") || target.match?(/\A[a-z][a-z0-9+.-]*:/i)

    target_path = target.split("#", 2).first
    next if target_path.empty?

    resolved = File.expand_path(target_path, File.dirname(path))
    target_relative = relative_to_root(resolved)
    fail_with("#{relative}: local link target #{target} is missing") unless File.exist?(resolved)
    fail_with("#{relative}: local link target #{target} is not tracked") unless tracked_file_set.include?(target_relative)
  end
end

puts "Validated #{SKILL_FILES.length} skills."
