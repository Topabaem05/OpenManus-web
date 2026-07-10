import argparse
import asyncio

from app.agent.manus import Manus
from app.logger import logger
from app.runtime.events import ConsoleEventSink
from app.runtime.runner import TaskRunner


async def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Run Manus agent with a prompt")
    parser.add_argument(
        "--prompt", type=str, required=False, help="Input prompt for the agent"
    )
    parser.add_argument(
        "--events",
        action="store_true",
        default=False,
        help="Output structured JSON events for each agent step",
    )
    args = parser.parse_args()

    # Create and initialize Manus agent
    agent = await Manus.create()
    try:
        # Use command line prompt if provided, otherwise ask for input
        prompt = args.prompt if args.prompt else input("Enter your prompt: ")
        if not prompt.strip():
            logger.warning("Empty prompt provided.")
            return

        if args.events:
            # Event-based mode: use TaskRunner with ConsoleEventSink
            sink = ConsoleEventSink()
            runner = TaskRunner(agent=agent, event_sink=sink)
            await runner.run(prompt)
        else:
            # Default mode: existing behavior
            logger.warning("Processing your request...")
            await agent.run(prompt)
            logger.info("Request processing completed.")
    except KeyboardInterrupt:
        logger.warning("Operation interrupted.")
    finally:
        # Ensure agent resources are cleaned up before exiting
        await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
