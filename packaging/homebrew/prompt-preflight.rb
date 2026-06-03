# Homebrew formula. After the first PyPI release, fill in `url`/`sha256` from the sdist
# (`brew create https://files.pythonhosted.org/.../prompt_preflight-X.Y.Z.tar.gz` helps),
# then host this in a tap repo (e.g. AnotherSamWithADream/homebrew-tap) so users can run:
#   brew install AnotherSamWithADream/tap/prompt-preflight
class PromptPreflight < Formula
  include Language::Python::Virtualenv

  desc "Rewrite rough prompts into clearer ones with Claude Haiku before a stronger model"
  homepage "https://github.com/AnotherSamWithADream/prompt-preflight"
  url "https://files.pythonhosted.org/packages/source/p/prompt-preflight/prompt_preflight-0.2.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "prompt-preflight", shell_output("#{bin}/enhance-cli --version")
  end
end
